// The editor page. The maths and the request building live in editor-core.js
// (tested under node), the per-part scene in editor-scene.js; this file is the
// three.js and DOM glue around them, and the postMessage bridge to the page that embeds it.
//
// Flow: GET the editor session -> flatten its MPD into placements -> show them, one group per part. A click
// picks a part (the ray is tested against each part's own bounding box, see EditorCore.pick). An edit is
// shown at once (the part moves, vanishes or changes colour), sent as ONE request, and the answer is applied as
// a difference: only the parts that really changed are touched. A failed edit rolls the picture back to the
// last answer from the server.
//
// Modes: edit (tools), view (look only), player (the step-by-step player on its own canvas). `mode=` sets
// the first one, the obd:command `set-mode` changes it without reloading the model.
//
// Scene bookkeeping: ldbi flips Y and Z and recentres the model, so scene
// coordinates differ from LDraw's. `offset` is that recentring; EditorCore's
// sceneToLdraw / ldrawToScene convert.
(function () {
  const { params, statusSetter } = ViewerCommon;
  const Core = EditorCore;
  const LIB = '/ldraw';
  const embed = params.get('embed') === '1';        // inside the build workspace: no toolbar, no panels, just the canvas
  const spaceParam = params.get('space'), idParam = params.get('id');
  const buildId = params.get('build') || (spaceParam === 'builds' ? idParam : null);   // a build in My Builds (no chat), else a chat
  const chatId = buildId || (spaceParam === 'chats' ? idParam : null) || params.get('chat');
  const apiBase = buildId ? `/api/builds/${encodeURIComponent(buildId)}` : `/api/chats/${encodeURIComponent(chatId || '')}`;
  const stage = document.getElementById('stage');
  const status = document.getElementById('status');
  const setStatus = statusSetter(status);
  const $ = id => document.getElementById(id);
  const buttons = { undo: $('undo'), redo: $('redo'), del: $('delete'), widen: $('widen'), publish: $('publish'), home: $('home') };
  const toolButtons = document.querySelectorAll('button[data-tool]');
  document.body.classList.toggle('embed', embed);

  // ---- the parent page --------------------------------------------------------------------------

  // Messages to the page that embeds this one (same origin only). Nothing is sent when the page is opened on its own.
  function post(message) {
    if (window.parent === window) return;
    try { window.parent.postMessage(message, location.origin); } catch (e) { /* the parent went away */ }
  }
  // Show a problem on the canvas and tell the parent.
  function fail(message) {
    setStatus(message, true);
    post({ type: 'obd:error', message: String(message) });
  }

  if (!chatId) { fail('No chat given: add ?chat=<chat id> (or ?space=builds&id=<slug>)'); return; }
  const chatLink = $('chat-link');
  chatLink.href = buildId ? `/build/${encodeURIComponent(buildId)}` : `/chat/${encodeURIComponent(chatId)}`;
  if (buildId) chatLink.textContent = 'Back to the build';
  if (buildId) $('open-other').hidden = true;
  chatLink.hidden = false;
  if (!WEBGL.isWebGLAvailable()) { fail('WebGL is not available in this browser.'); return; }

  const narrow = matchMedia('(max-width: 560px), (pointer: coarse)');
  // The workspace decides what a phone may do (it sends mode=view); on its own the page keeps the old rule.
  const deviceOk = () => embed || !narrow.matches;
  function showDeviceNote() {
    document.body.classList.toggle('readonly', !deviceOk() || state.readonly);
    $('desktop-note').hidden = deviceOk();
  }
  narrow.addEventListener('change', () => { showDeviceNote(); refreshControls(); });

  // ---- API ----------------------------------------------------------------

  async function call(path, body, method) {
    const response = await fetch(`${apiBase}/editor${path}`, {
      method: method || (body === undefined ? 'GET' : 'POST'),
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload.detail;
      const error = new Error(typeof detail === 'string' ? detail : (detail && detail.detail) || response.statusText);
      error.status = response.status;
      error.revision = detail && typeof detail === 'object' ? detail.revision : undefined;
      throw error;
    }
    return payload;
  }

  // ---- ldbi set-up (the viewer's own settings, Normal rendering) ---------------

  LDR.Options.bgColor = ViewerCommon.stageColor;        // the backdrop follows ?theme=dark
  LDR.Colors.envMapPrefix = 'vendor/ldbi/textures/cube/';
  LDR.Colors.textureMaterialPrefix = 'vendor/ldbi/textures/materials/';
  // Same perspective fix for conditional lines as viewer.js (see there).
  const conditionalShader = LDR.Shader.createConditionalVertexShader;
  LDR.Shader.createConditionalVertexShader = function () {
    const src = conditionalShader.call(this);
    const clip = 'vec2 xp1=gl_Position.xy;vec2 d12=vec4(m*vec4(p2,1.0)).yx-xp1.yx;d12.y=-d12.y;vec2 d13=vec4(m*vec4(p3,1.0)).xy-xp1;vec2 d14=vec4(m*vec4(p4,1.0)).xy-xp1;';
    const ndc = 'vec2 xp1=gl_Position.xy/gl_Position.w;vec4 q2=m*vec4(p2,1.0);vec4 q3=m*vec4(p3,1.0);vec4 q4=m*vec4(p4,1.0);' +
                'vec2 d12=q2.yx/q2.w-xp1.yx;d12.y=-d12.y;vec2 d13=q3.xy/q3.w-xp1;vec2 d14=q4.xy/q4.w-xp1;';
    if (src.includes(clip)) LDR.Shader.COND = src.replace(clip, ndc);
    else if (!src.includes(ndc)) console.warn('ldbi conditional line shader changed; perspective fix not applied');
    return LDR.Shader.COND || src;
  };
  const parseColor = THREE.LDRLoader.prototype.parseColor;
  THREE.LDRLoader.prototype.parseColor = function (colorID) {      // an unknown colour must not stop the model loading
    const direct = colorID.length === 9 && colorID.substring(0, 3) === '0x2';
    if (!direct && LDR.Colors[colorID] === undefined) return 0;
    return parseColor.call(this, colorID);
  };

  const FOV = 45;
  const clamp = THREE.MathUtils.clamp;
  const MANY_SELECTED = 300;     // beyond this the selection is outlined as one box
  const LIVE_LIMIT = 6000;       // above this many placements a drag moves the outline only, not the parts

  // ---- state ----------------------------------------------------------------

  const state = {
    session: null,        // the last response of the editor API
    shown: null,          // what is on screen: { scene, pane, leafScene, offset, placements, leaves, byKey, boxes, radius, center }
    nodes: [],            // the placements drawn (after an optimistic edit: what the edit should give); shown.placements
    confirmed: null,      // { session, nodes } as the server last said
    selected: [],
    editable: new Set(),  // keys of the placements that are entries of the plan
    tool: 'move',
    mode: Core.MODES.includes(params.get('mode')) ? params.get('mode') : 'edit',
    add: null,            // { part, colour }: what the add tool places
    colour: 4,            // the paint tool's colour
    hover: null,          // key of the part under the pointer
    readonly: false,      // a saved version is on screen: nothing can be edited
    busy: false,          // a request is in flight (the picture stays usable)
    stale: false,         // the last rebuild could not be drawn: block edits until the page is reloaded
    gesture: null,        // { down, drag, box }
    perf: [],             // timings of each edit, for the browser tests and the measurements
  };

  window.editorState = state;                     // read by the browser tests (which part is picked, where it is)

  const selecting = () => state.mode === 'edit';
  const editing = () => state.mode === 'edit' && !state.readonly && !state.stale && deviceOk();

  // ---- loading a scene --------------------------------------------------------

  const PLACEHOLDER = '0 FILE empty.ldr\n1 16 0 0 0 1 0 0 0 1 0 0 0 1 3005.dat\n';

  // A model of no parts has nothing for ldbi to load: give it a stand-in file (never drawn).
  function loadScene(mpd, hasParts) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(new Blob([hasParts ? mpd : PLACEHOLDER], { type: 'text/plain' }));
      const pane = document.createElement('div');
      pane.className = 'pane';
      pane.style.visibility = 'hidden';                // shown once built, so a failed build never flashes
      const canvas = document.createElement('canvas');
      pane.appendChild(canvas);
      stage.appendChild(pane);
      const idToUrl = id => (id === url ? [url] : [`/ldraw-id/${id}`]);
      const missing = new Set();
      let scene, started = false;
      const bail = message => {
        clearTimeout(timer);
        URL.revokeObjectURL(url);
        try { scene && scene.dispose && scene.dispose(); } catch (e) { /* ignore */ }
        pane.remove();
        reject(new Error(message));
      };
      // A load that never reports back (a throwing callback in the vendored scene code) must not leave the page stuck.
      const timer = setTimeout(() => bail('Loading the model timed out'), 60000);
      LDR.Colors.load(() => {
        try {
          scene = new LDrawOrg.Model(canvas, url, {
            idToUrl, idToTextureUrl: id => `${LIB}/parts/textures/${id.toLowerCase()}`,
            onProgress: () => {},
            physicalRenderingAge: 0,
          });
          window.scene = scene;                       // ldraworgscene.js refers to a global `scene` (viewer.js declares it too)
          scene.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
          scene.renderer.shadowMap.enabled = false;
          usePerspective(scene);
          // ldbi would now build the whole model into its own meshes. We build the parts ourselves (editor-scene.js), so
          // this runs when the loader has the part files: set the stage and hand the scene over. Later calls are the
          // loader finishing something we asked for (a part the model did not use yet), see ensureParts.
          scene.onLoad = function () {
            if (scene.pendingLoad) { const done = scene.pendingLoad; scene.pendingLoad = null; done(); return; }
            if (started) return;
            started = true;
            try {
              clearTimeout(timer);
              URL.revokeObjectURL(url);
              this.scene.background = new THREE.Color(LDR.Options.bgColor);
              this.baseObject = new THREE.Group();
              this.scene.add(this.baseObject);
              this.mc = new LDR.MeshCollector(new THREE.Group(), new THREE.Group(), new THREE.Group());   // keeps the glow list only
              resolve({ scene, pane, missing });
            } catch (e) { bail(String(e.message || e)); }
          };
          scene.loader.onError = e => {
            if (e && e.subModel === url) bail('The model could not be loaded');
            else if (e && e.subModel) missing.add(e.subModel);
          };
          scene.loader.onWarning = () => {};
        } catch (e) { bail(String(e.message || e)); }
      }, () => bail('Could not load LDConfig.ldr (colour definitions).'), `${LIB}/LDConfig.ldr`);
    });
  }

  // Load part files the scene has not seen (a part added for the first time). Resolves when the loader is done.
  function ensureParts(shown, refs) {
    const { scene } = shown;
    return new Promise(resolve => {
      const timer = setTimeout(() => { scene.pendingLoad = null; resolve(); }, 20000);
      scene.pendingLoad = () => { clearTimeout(timer); resolve(); };
      for (const ref of refs) scene.loader.load(ref.toLowerCase());
    });
  }

  // A perspective camera with orbit controls, as in viewer.js (inspect mode only).
  function usePerspective(scene) {
    const camera = new THREE.PerspectiveCamera(FOV, 1, 1, 100000);
    scene.orbitControls?.dispose();
    scene.camera = camera;
    const controls = new THREE.OrbitControls(camera, scene.renderer.domElement);
    controls.addEventListener('change', () => scene.render());
    controls.screenSpacePanning = true;
    controls.zoomSpeed = 3;
    controls.mouseButtons.RIGHT = -1;
    scene.orbitControls = controls;
    scene.render = function () {
      const close = camera.position.distanceTo(controls.target);
      const near = clamp(close * 0.05, 0.5, 10);
      const far = Math.max(close * 4, 5000) + near;
      if (camera.near !== near || camera.far !== far) { camera.near = near; camera.far = far; camera.updateProjectionMatrix(); }
      if (scene.overlay) scaleGizmo(scene);
      if (this.composer) this.composer.render();
      else this.renderer.render(this.scene, camera);
    };
    scene.onChange = function () {
      if (!this.baseObject) return;
      const w = this.container.clientWidth, h = this.container.clientHeight;
      this.renderer.setSize(w, h);
      camera.aspect = w / Math.max(1, h);
      camera.updateProjectionMatrix();
      if (!this.framed && this.onFirstFrame) { this.framed = true; this.onFirstFrame(); }
      controls.update();
      this.composer = null;
      if (this.mc && this.mc.glowObjects.length) {            // glowing (emissive) colours need the outline passes
        this.composer = new THREE.EffectComposer(this.renderer);
        this.composer.addPass(new THREE.RenderPass(this.scene, camera));
        if (!this.mc.attachGlowPasses(w, h, this.scene, camera, this.composer)) {
          this.composer.renderTarget1.dispose();
          this.composer.renderTarget2.dispose();
          this.composer = null;
        }
      }
      this.render();
    };
  }

  // Frame the model from ldbi's usual viewpoint, or restore the previous camera (kept in LDraw coordinates).
  function frame(shown, previous) {
    const { scene } = shown;
    const box = new THREE.Box3().setFromObject(scene.baseObject);
    if (box.isEmpty()) { shown.center.set(0, 0, 0); shown.radius = 100; }
    else {
      box.getCenter(shown.center);
      shown.radius = Math.max(10, box.getSize(new THREE.Vector3()).length() / 2);
    }
    const camera = scene.camera, controls = scene.orbitControls;
    if (previous && !box.isEmpty()) {
      camera.position.fromArray(Core.ldrawToScene(previous.position, shown.offset.toArray()));
      controls.target.fromArray(Core.ldrawToScene(previous.target, shown.offset.toArray()));
    } else resetView(shown);
    camera.lookAt(controls.target);
    controls.update();
    scene.render();
  }

  function resetView(shown) {
    const camera = shown.scene.camera, controls = shown.scene.orbitControls;
    const distance = Core.fitDistance(shown.radius, FOV, camera.aspect, Core.framingFill(shown.radius));
    camera.position.copy(shown.center).addScaledVector(new THREE.Vector3(1, 0.7, 1).normalize(), distance);
    controls.target.copy(shown.center);
    camera.lookAt(shown.center);
    controls.update();
    shown.scene.render();
  }

  // Bring the selection (or, with nothing selected, the model) into view, keeping the viewing direction.
  function frameSelection() {
    const shown = state.shown;
    if (!shown) return;
    const chosen = state.selected.map(k => shown.byKey.get(k)).filter(Boolean);
    if (!chosen.length) { resetView(shown); return; }
    const box = new THREE.Box3();
    for (const n of chosen) {
      const b = n.leaf ? shown.boxes[n.ref] : Core.subtreeBox(shown.placements, n, shown.boxes);
      const local = b || FALLBACK_BOX;
      for (const x of [local.min[0], local.max[0]]) for (const y of [local.min[1], local.max[1]]) for (const z of [local.min[2], local.max[2]]) {
        const w = Core.mulVec(n.matrix, [x, y, z]).map((v, k) => v + n.position[k]);
        box.expandByPoint(new THREE.Vector3(...Core.ldrawToScene(w, shown.offset.toArray())));
      }
    }
    const camera = shown.scene.camera, controls = shown.scene.orbitControls;
    const centre = box.getCenter(new THREE.Vector3());
    const radius = Math.max(40, box.getSize(new THREE.Vector3()).length() / 2);
    const direction = camera.position.clone().sub(controls.target).normalize();
    const distance = Core.fitDistance(radius, FOV, camera.aspect, 0.5);
    controls.target.copy(centre);
    camera.position.copy(centre).addScaledVector(direction, distance);
    camera.lookAt(centre);
    controls.update();
    shown.scene.render();
  }

  // ---- part boxes (for picking) ---------------------------------------------

  const FALLBACK_BOX = { min: [-10, -10, -10], max: [10, 10, 10] };

  // The part's bounding box in its own LDraw frame; a submodel is the union of its parts' boxes.
  function partBox(loader, id, depth = 0) {
    const type = loader.getPartType(id.toLowerCase());
    if (!type || depth > 8) return null;
    const own = type.geometry && type.geometry.boundingBox;
    // ldbi's part geometry is in the frame the scene's part groups transform with sceneMatrix (rows negated), and outline()
    // negates the same rows: the box must stay unflipped or the outline sits one brick height off the part.
    if (own && !own.isEmpty()) {
      return own.clone();
    }
    const box = new THREE.Box3();
    for (const step of type.steps || []) {
      for (const pd of step.subModels || []) {
        const child = partBox(loader, pd.ID, depth + 1);
        if (!child) continue;
        const e = pd.r.elements;
        for (const x of [child.min.x, child.max.x]) for (const y of [child.min.y, child.max.y]) for (const z of [child.min.z, child.max.z]) {
          box.expandByPoint(new THREE.Vector3(e[0] * x + e[3] * y + e[6] * z + pd.p.x,
                                              e[1] * x + e[4] * y + e[7] * z + pd.p.y,
                                              e[2] * x + e[5] * y + e[8] * z + pd.p.z));
        }
      }
    }
    return box.isEmpty() ? null : box;
  }

  // Boxes for the references that have none yet (shown.boxes grows as parts are added).
  function addBoxes(shown, placements) {
    for (const p of placements) {
      if (shown.boxes[p.ref]) continue;
      let box = null;
      try {
        // A part's own box needs its geometry; the scene builds it when it first draws the part.
        if (p.leaf) EditorScene.partType(shown.scene.loader, p.ref)?.ensureGeometry?.(shown.scene.loader);
        box = partBox(shown.scene.loader, p.ref);
      } catch (e) { box = null; }
      shown.boxes[p.ref] = box ? { min: box.min.toArray(), max: box.max.toArray() } : FALLBACK_BOX;
    }
  }

  // ---- showing a session --------------------------------------------------------

  function cameraInLdraw(shown) {
    const offset = shown.offset.toArray();
    return {
      position: Core.sceneToLdraw(shown.scene.camera.position.toArray(), offset),
      target: Core.sceneToLdraw(shown.scene.orbitControls.target.toArray(), offset),
    };
  }

  function retire(shown) {
    shown.pane.remove();
    try { shown.leafScene && shown.leafScene.dispose(); } catch (e) { /* ignore */ }
    try { shown.scene.dispose && shown.scene.dispose(); } catch (e) { /* ignore */ }
    try { shown.scene.renderer.dispose(); shown.scene.renderer.forceContextLoss(); } catch (e) { /* ignore */ }
  }

  function indexNodes(shown, nodes) {
    shown.placements = nodes;
    shown.leaves = nodes.filter(p => p.leaf);
    shown.byKey = new Map(nodes.map(p => [p.key, p]));
    state.nodes = nodes;
    state.editable = new Set(nodes.filter(p => p.editable).map(p => p.key));
  }

  // The scene's glow list changed: the post-processing passes are rebuilt.

  // Put `nodes` on the stage (parts only; no bookkeeping). Every part type must be loaded.
  // `later`: the caller renders next (drawOverlay does), so skip the render here.
  function paint(nodes, later) {
    const shown = state.shown;
    const { glowChanged } = shown.leafScene.sync(nodes);
    if (glowChanged) shown.scene.onChange();
    else if (!later) shown.scene.render();
  }

  // Make `nodes` the model: draw them, load the part files they need first, and update everything that depends on them.
  async function setNodes(nodes) {
    const shown = state.shown;
    const missing = shown.leafScene.missing(nodes.filter(n => n.leaf));
    if (missing.length) await ensureParts(shown, missing);
    if (state.shown !== shown) return;                       // another scene took over while the files loaded
    addBoxes(shown, nodes);
    indexNodes(shown, nodes);
    paint(nodes, true);
    state.selected = state.selected.filter(key => shown.byKey.has(key));
    if (state.hover && !shown.byKey.has(state.hover)) state.hover = null;
  }

  // Draw a whole model on a scene of its own (the first model, a saved version). The previous picture stays until it is built.
  async function showFull(session, { reframe, keepOld } = {}) {
    const old = state.shown;
    const previous = old && !reframe ? cameraInLdraw(old) : null;
    const flat = Core.flatten(session.mpd, session.plan);
    let built;
    try {
      built = await loadScene(session.mpd, flat.nodes.length > 0);
    } catch (e) {
      state.stale = true;
      state.session = session;
      refreshControls();
      fail(`Could not draw the rebuilt model (${e.message}). Showing the last good picture; reload the page to continue.`);
      return false;
    }
    const { scene, pane, missing } = built;
    const shown = {
      scene, pane, leafScene: EditorScene.create(scene), boxes: {}, missing,
      center: new THREE.Vector3(), radius: 100, offset: new THREE.Vector3(), readonly: !session.plan,
    };
    try { scene.loader.loadTexmaps(); } catch (e) { /* no decals */ }
    state.shown = shown;                                      // setNodes / paint work on the new scene from here
    addBoxes(shown, flat.nodes);
    indexNodes(shown, flat.nodes);
    shown.leafScene.sync(flat.nodes);
    const bounds = shown.leafScene.bounds();
    scene.mc.boundingBox = bounds.isEmpty() ? new THREE.Box3(new THREE.Vector3(), new THREE.Vector3(1, 1, 1)) : bounds;
    scene.build();                                            // lights and the camera's starting place, sized to the model
    const middle = scene.mc.boundingBox.getCenter(new THREE.Vector3());
    scene.baseObject.position.set(-middle.x, -middle.y, -middle.z);
    shown.offset = scene.baseObject.position.clone();
    scene.onFirstFrame = () => frame(shown, previous);
    pane.style.visibility = 'visible';
    scene.onChange();
    LDR.Colors.loadTextures(() => scene.render());            // ldbi repaints when its colour textures arrive
    if (old) {
      if (keepOld) old.pane.style.display = 'none'; else retire(old);
    }
    state.stale = false;
    state.selected = [];
    state.hover = null;
    shown.truncated = flat.truncated;
    return true;
  }

  // The server's answer to an edit, an undo, a reload: draw what changed.
  // `nodes`: the picture already worked out from the server's difference (then there is no MPD to read).
  async function applyConfirmed(session, reframe, nodes) {
    const started = performance.now();
    const flat = nodes ? { nodes, truncated: state.shown ? state.shown.truncated : false } : Core.flatten(session.mpd, session.plan);
    state.session = session;
    if (!state.shown) {
      if (!await showFull(session, { reframe: true })) return false;
    } else if (reframe) {
      await setNodes(flat.nodes);
      resetView(state.shown);
    } else {
      await slideIn(flat.nodes);
      await setNodes(flat.nodes);
    }
    state.confirmed = { session, nodes: flat.nodes };
    state.stale = false;
    state.shown.truncated = flat.truncated;
    afterShow();
    return performance.now() - started;
  }

  // A part that appeared where it was dropped slides to where the server put it (a snap moves it a few studs).
  const TWEEN_MS = 120;
  async function slideIn(finalNodes) {
    const shown = state.shown;
    const from = state.nodes || [];
    const pending = new Map(from.filter(n => n.pending).map(n => [n.key, n]));
    if (!pending.size || document.hidden || !finalNodes.some(n => {
      const a = pending.get(n.key);
      return a && Math.hypot(n.position[0] - a.position[0], n.position[1] - a.position[1], n.position[2] - a.position[2]) > 0.5;
    })) return;
    const t0 = performance.now();
    for (;;) {
      const t = Math.min(1, (performance.now() - t0) / TWEEN_MS);
      if (state.shown !== shown || t >= 1) return;
      paint(Core.tweenNodes(from, finalNodes, 1 - (1 - t) * (1 - t)), false);
      await new Promise(resolve => requestAnimationFrame(resolve));
    }
  }

  function afterShow() {
    const session = state.session;
    if (state.tool !== 'add' || !editing()) clearGhost();
    drawOverlay();
    refreshControls();
    $('file').textContent = (session && session.name) || 'manual model';
    setRestingStatus();
    if (state.mode === 'player') enterPlayer();               // the player shows a copy of the model: refresh it
  }

  // The nodes as the server last confirmed them (after a failed edit).
  async function rollback() {
    if (!state.confirmed || !state.shown) return;
    await setNodes(state.confirmed.nodes);
    afterShow();
  }

  function refreshControls() {
    const s = state.session;
    const idle = !state.busy && editing();
    buttons.undo.disabled = !(idle && s && s.undo_depth > 0);
    buttons.redo.disabled = !(idle && s && s.redo_depth > 0);
    buttons.del.disabled = !(idle && state.selected.length);
    buttons.widen.disabled = !(idle && s && state.shown && state.selected.some(k => (state.shown.byKey.get(k) || {}).parentKey));
    buttons.publish.disabled = !(idle && s);
    buttons.home.disabled = !state.shown;
    $('open-other').disabled = !(idle && s);
    toolButtons.forEach(b => {
      b.setAttribute('aria-pressed', String(b.dataset.tool === state.tool));
      b.disabled = !editing();
    });
    showDeviceNote();
  }

  // ---- overlay: selection boxes, hover, the add tool's ghost, and the gizmo -----------------------

  const AXIS_COLOURS = { x: 0xe03131, y: 0x2f9e44, z: 0x1c7ed6 };
  const SELECT_COLOUR = 0xff8800, HOVER_COLOUR = 0x3b8bff, PENDING_COLOUR = 0x8a94a6;
  const sceneAxis = axis => { const a = Core.AXES[axis]; return new THREE.Vector3(a[0], -a[1], -a[2]); };   // LDraw axis in scene space

  // A box outline in the frame of a placement (matrix m, 9 numbers, at LDraw position `at`).
  function outline(box, m, at, colour, dashed) {
    const group = new THREE.Group();
    group.matrixAutoUpdate = false;
    const t = Core.ldrawToScene(at, state.shown.offset.toArray());
    group.matrix.set(m[0], m[1], m[2], t[0],
                     -m[3], -m[4], -m[5], t[1],
                     -m[6], -m[7], -m[8], t[2],
                     0, 0, 0, 1);
    const size = [0, 1, 2].map(k => Math.max(box.max[k] - box.min[k], 1));
    const material = dashed
      ? new THREE.LineDashedMaterial({ color: colour, depthTest: false, dashSize: 4, gapSize: 3 })
      : new THREE.LineBasicMaterial({ color: colour, depthTest: false });
    const lines = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(size[0], size[1], size[2])), material);
    if (dashed) lines.computeLineDistances();
    lines.position.set((box.min[0] + box.max[0]) / 2, (box.min[1] + box.max[1]) / 2, (box.min[2] + box.max[2]) / 2);
    lines.renderOrder = 998;
    group.add(lines);
    return group;
  }

  const boxOf = (shown, p) => (p.leaf ? shown.boxes[p.ref] : Core.subtreeBox(shown.placements, p, shown.boxes) || shown.boxes[p.ref]) || FALLBACK_BOX;

  function drawOverlay() {
    const shown = state.shown;
    if (!shown) return;
    const { scene } = shown;
    if (scene.overlay) { scene.scene.remove(scene.overlay); }
    const overlay = new THREE.Group();
    scene.overlay = overlay;
    shown.selectionBoxes = null;
    shown.gizmo = null;
    const chosen = selecting() ? state.selected.map(k => shown.byKey.get(k)).filter(Boolean) : [];
    if (chosen.length) {
      const boxes = new THREE.Group();
      if (chosen.length > MANY_SELECTED) {              // thousands of outlines are noise and slow: one box round them all
        const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
        for (const p of chosen) {
          const b = boxOf(shown, p);
          for (const x of [b.min[0], b.max[0]]) for (const y of [b.min[1], b.max[1]]) for (const z of [b.min[2], b.max[2]]) {
            const w = Core.mulVec(p.matrix, [x, y, z]);
            for (let k = 0; k < 3; k++) { const v = w[k] + p.position[k]; min[k] = Math.min(min[k], v); max[k] = Math.max(max[k], v); }
          }
        }
        boxes.add(outline({ min: [-(max[0] - min[0]) / 2, -(max[1] - min[1]) / 2, -(max[2] - min[2]) / 2], max: [(max[0] - min[0]) / 2, (max[1] - min[1]) / 2, (max[2] - min[2]) / 2] },
                          [1, 0, 0, 0, 1, 0, 0, 0, 1], [0, 1, 2].map(k => (min[k] + max[k]) / 2), SELECT_COLOUR, true));
      } else {
        for (const p of chosen) boxes.add(outline(boxOf(shown, p), p.matrix, p.position, p.editable || state.readonly ? SELECT_COLOUR : 0x888888, true));
      }
      boxes.matrixAutoUpdate = false;
      overlay.add(boxes);
      shown.selectionBoxes = boxes;
      const pivot = Core.centroid(shown.placements, state.selected.filter(key => state.editable.has(key)));
      if (pivot && (state.tool === 'move' || state.tool === 'rotate') && editing()) {
        const gizmo = makeGizmo(state.tool);
        gizmo.position.fromArray(Core.ldrawToScene(pivot, shown.offset.toArray()));
        overlay.add(gizmo);
        shown.gizmo = gizmo;
        shown.pivot = pivot;
      }
    }
    const pending = shown.leaves.filter(p => p.pending);        // parts shown before the server has confirmed them
    if (pending.length && pending.length <= MANY_SELECTED) {
      const group = new THREE.Group();
      group.matrixAutoUpdate = false;
      for (const p of pending) group.add(outline(boxOf(shown, p), p.matrix, p.position, PENDING_COLOUR, true));
      overlay.add(group);
    }
    scene.scene.add(overlay);
    scaleGizmo(scene);
    drawHover();
    drawGround();
    scene.render();
    postState();
    emitSelection();
  }

  // The part under the pointer, outlined in blue (one object, redrawn when the part under the pointer changes).
  function drawHover() {
    const shown = state.shown;
    if (!shown) return;
    if (shown.hoverBox) { shown.scene.scene.remove(shown.hoverBox); shown.hoverBox = null; }
    const p = state.hover && selecting() ? shown.byKey.get(state.hover) : null;
    if (!p || state.selected.includes(p.key)) return;
    shown.hoverBox = outline(boxOf(shown, p), p.matrix, p.position, HOVER_COLOUR, false);
    shown.scene.scene.add(shown.hoverBox);
  }

  // A grid at the model's lowest point: shown while placing parts, and always for a model that has none yet.
  function drawGround() {
    const shown = state.shown;
    if (!shown) return;
    if (shown.grid) { shown.scene.scene.remove(shown.grid); shown.grid = null; }
    const wanted = selecting() && (state.tool === 'add' || !shown.leaves.length) && editing();
    if (!wanted) return;
    const cell = shown.radius > 400 ? 80 : 20;
    const half = Math.max(5, Math.ceil(Math.max(shown.radius, 100) * 1.4 / cell)) * cell;
    const dark = ViewerCommon.darkStage;
    const grid = new THREE.GridHelper(half * 2, half * 2 / cell, dark ? 0x3a5078 : 0x9a9a9a, dark ? 0x22334f : 0xcfcfcf);
    grid.material.transparent = true;
    grid.material.opacity = 0.7;
    const floor = groundY();
    const centre = Core.ldrawToScene([0, floor, 0], shown.offset.toArray());
    grid.position.set(shown.center.x, centre[1], shown.center.z);
    shown.grid = grid;
    shown.scene.scene.add(grid);
  }

  // LDraw y of the model's lowest point (y points down): where a first brick rests.
  function groundY() {
    const shown = state.shown;
    let lowest = 0, any = false;
    for (const n of shown.leaves) {
      const box = shown.boxes[n.ref];
      if (!box) continue;
      for (const x of [box.min[0], box.max[0]]) for (const y of [box.min[1], box.max[1]]) for (const z of [box.min[2], box.max[2]]) {
        const w = Core.mulVec(n.matrix, [x, y, z])[1] + n.position[1];
        if (!any || w > lowest) { lowest = w; any = true; }
      }
    }
    return lowest;
  }

  // ---- bridge to the page that embeds this one -------------------------------
  // The old React editor page (palette): it needs the live plan, used rows and selection, and asks for a reload
  // after it posts edits of its own. {source: 'ldraw-editor'} messages; kept as they were.
  function postState() {
    const s = state.session;
    if (window.parent === window || !s) return;
    const chosen = state.shown ? state.selected.map(k => state.shown.byKey.get(k)).filter(Boolean) : [];
    window.parent.postMessage({
      source: 'ldraw-editor', type: 'state', revision: s.revision, plan: s.plan, parts: s.parts,
      selection: chosen.map(p => ({ id: p.entryId, position: p.localPosition, section: p.section, leaf: p.leaf, repeated: p.repeated })),
    }, location.origin);
  }

  // The build workspace: what is selected, as obd:selection (only when it changed).
  let lastSelection = '[]';
  function nameOf(p) {
    if (!p.leaf) return `${p.ref}`;
    const type = EditorScene.partType(state.shown.scene.loader, p.ref);
    return (type && (type.modelDescription || type.name)) || p.ref;
  }
  function emitSelection() {
    const shown = state.shown;
    if (!shown || window.parent === window) return;
    const chosen = selecting() ? state.selected.map(k => shown.byKey.get(k)).filter(Boolean) : [];
    const pieces = chosen.map(p => Core.pieceInfo(p, nameOf(p)));
    const signature = JSON.stringify(pieces);
    if (signature === lastSelection) return;
    lastSelection = signature;
    post({ type: 'obd:selection', pieces });
  }

  function handleMaterial(colour) {
    return new THREE.MeshBasicMaterial({ color: colour, depthTest: false, transparent: true });
  }
  const hitMaterial = () => new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false, depthTest: false });

  function makeGizmo(tool) {
    const gizmo = new THREE.Group();
    for (const axis of ['x', 'y', 'z']) {
      const direction = sceneAxis(axis);
      const tag = mesh => { mesh.userData.axis = axis; mesh.renderOrder = 999; return mesh; };
      if (tool === 'move') {
        const shaft = tag(new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.025, 0.8, 8), handleMaterial(AXIS_COLOURS[axis])));
        const tip = tag(new THREE.Mesh(new THREE.ConeGeometry(0.08, 0.2, 12), handleMaterial(AXIS_COLOURS[axis])));
        const hit = tag(new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.1, 1.0, 6), hitMaterial()));
        const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction);
        for (const [mesh, at] of [[shaft, 0.4], [tip, 0.9], [hit, 0.5]]) {
          mesh.quaternion.copy(q);
          mesh.position.copy(direction).multiplyScalar(at);
          gizmo.add(mesh);
        }
      } else {
        const ring = tag(new THREE.Mesh(new THREE.TorusGeometry(1, 0.02, 8, 64), handleMaterial(AXIS_COLOURS[axis])));
        const hit = tag(new THREE.Mesh(new THREE.TorusGeometry(1, 0.09, 6, 48), hitMaterial()));
        for (const mesh of [ring, hit]) {
          mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), direction);
          gizmo.add(mesh);
        }
      }
    }
    return gizmo;
  }

  // Constant size on screen.
  function scaleGizmo(scene) {
    const shown = state.shown;
    if (!shown || shown.scene !== scene || !shown.gizmo) return;
    const distance = scene.camera.position.distanceTo(shown.gizmo.position);
    shown.gizmo.scale.setScalar(Math.max(distance * 0.16, 1));
  }

  // ---- rays ----------------------------------------------------------------------

  function pointerRay(e) {
    const shown = state.shown, camera = shown.scene.camera;
    const rect = shown.scene.renderer.domElement.getBoundingClientRect();
    camera.updateMatrixWorld();
    const ndc = new THREE.Vector2((e.clientX - rect.left) / rect.width * 2 - 1, 1 - (e.clientY - rect.top) / rect.height * 2);
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(ndc, camera);
    const offset = shown.offset.toArray();
    return {
      scene: raycaster,
      ldraw: { origin: Core.sceneToLdraw(raycaster.ray.origin.toArray(), offset),
               direction: Core.directionToLdraw(raycaster.ray.direction.toArray()) },
    };
  }

  function gizmoHit(ray) {
    const shown = state.shown;
    if (!shown.gizmo) return null;
    shown.gizmo.updateMatrixWorld(true);
    const hits = ray.scene.intersectObjects(shown.gizmo.children, false);
    return hits.length ? hits[0].object.userData.axis : null;
  }

  // A point in the root's LDraw frame -> [x, y, w] with x, y in -1..1 (w > 0: in front of the camera).
  function projector() {
    const shown = state.shown, camera = shown.scene.camera, offset = shown.offset.toArray();
    camera.updateMatrixWorld();
    const view = camera.matrixWorldInverse, projection = camera.projectionMatrix;
    const v = new THREE.Vector4();
    return point => {
      const s = Core.ldrawToScene(point, offset);
      v.set(s[0], s[1], s[2], 1).applyMatrix4(view).applyMatrix4(projection);
      return v.w > 0 ? [v.x / v.w, v.y / v.w, v.w] : null;
    };
  }

  // ---- pointer handling ------------------------------------------------------------

  const CLICK_PIXELS = 4;
  const marquee = $('marquee'), snapChip = $('snap');
  const showSnap = text => { snapChip.textContent = text || ''; snapChip.hidden = !text; };

  stage.addEventListener('pointerdown', e => {
    stage.focus({ preventScroll: true });
    const shown = state.shown;
    if (!shown || e.button !== 0 || !selecting()) return;
    state.gesture = { x: e.clientX, y: e.clientY, shift: e.shiftKey, drag: null, box: null };
    const ray = pointerRay(e);
    // Ctrl (or Cmd) and a drag: a rectangle that selects what is inside it. The orbit must not see the press (it would pan).
    if ((e.ctrlKey || e.metaKey) && ['select', 'move', 'rotate'].includes(state.tool)) {
      shown.scene.orbitControls.enabled = false;
      stage.setPointerCapture(e.pointerId);
      state.gesture.box = { x0: e.clientX, y0: e.clientY, x1: e.clientX, y1: e.clientY, add: e.shiftKey };
      return;
    }
    if (!editing()) return;
    const axis = gizmoHit(ray);
    if (!axis) return;
    // Our drag, not the orbit's: switch the controls off before they see this event.
    shown.scene.orbitControls.enabled = false;
    stage.setPointerCapture(e.pointerId);
    const direction = Core.AXES[axis];
    state.gesture.drag = {
      kind: state.tool, axis, startRay: ray.ldraw, pivot: shown.pivot,
      start: state.tool === 'move' ? Core.axisParameter(ray.ldraw, shown.pivot, direction) : 0,
      amount: 0,
    };
  }, { capture: true });

  stage.addEventListener('pointermove', e => {
    const g = state.gesture, shown = state.shown;
    if (!shown) return;
    if (g && g.box) {
      g.box.x1 = e.clientX; g.box.y1 = e.clientY;
      const b = g.box;
      Object.assign(marquee.style, { left: `${Math.min(b.x0, b.x1)}px`, top: `${Math.min(b.y0, b.y1)}px`,
                                     width: `${Math.abs(b.x1 - b.x0)}px`, height: `${Math.abs(b.y1 - b.y0)}px` });
      marquee.hidden = Math.hypot(b.x1 - b.x0, b.y1 - b.y0) <= CLICK_PIXELS;
      return;
    }
    if (g && g.drag) {
      const d = g.drag, ray = pointerRay(e).ldraw, free = e.altKey;
      if (d.kind === 'move') {
        const s = Core.axisParameter(ray, d.pivot, Core.AXES[d.axis]);
        d.amount = s === null || d.start === null ? 0 : Core.snapMove(d.axis, s - d.start, !free);
      } else {
        const angle = Core.planeAngle(d.startRay, ray, d.pivot, Core.AXES[d.axis]);
        d.amount = angle === null ? 0 : Core.snapRotate(angle, !free);
      }
      previewDrag(d);
      const text = d.kind === 'move' ? `Move ${d.axis.toUpperCase()} ${d.amount > 0 ? '+' : ''}${d.amount} LDU` : `Rotate ${d.axis.toUpperCase()} ${d.amount}°`;
      showSnap(`${text} · ${free ? 'free (Alt)' : `snap ${d.kind === 'move' ? `${Core.snapStep(d.axis)} LDU` : '15°'}, Alt = free`}`);
      setStatus(d.kind === 'move' ? `${text} (${free ? 'free, Alt held' : `snapping to ${Core.snapStep(d.axis)} LDU, hold Alt for free`})` : `${text} (${free ? 'free, Alt held' : 'snapping to 15°, hold Alt for free'})`, false);
      return;
    }
    if (!g && e.buttons === 0) pointerIdle(e);
  });

  stage.addEventListener('pointerleave', () => { setHover(null); clearGhost(); });

  // ---- hover and the add tool's ghost (one frame at a time) ------------------------------------

  let idleEvent = null, idleFrame = 0;
  function pointerIdle(e) {
    idleEvent = e;
    if (idleFrame) return;
    idleFrame = requestAnimationFrame(() => {
      idleFrame = 0;
      const ev = idleEvent;
      const shown = state.shown;
      if (!ev || !shown || state.gesture || !selecting()) return;
      const ray = pointerRay(ev).ldraw;
      if (state.tool === 'add' && editing()) { updateGhost(ray); setHover(null); return; }
      const hit = Core.pickHits(ray, shown.leaves, shown.boxes)[0];
      setHover(hit ? hit.p.key : null);
    });
  }
  function setHover(key) {
    if (state.hover === key) return;
    state.hover = key;
    drawHover();
    if (state.shown) state.shown.scene.render();
  }

  let ghostPlacement = null;
  function clearGhost() {
    const shown = state.shown;
    ghostPlacement = null;
    if (shown && shown.ghost) { shown.scene.scene.remove(shown.ghost); shown.ghost = null; shown.scene.render(); }
    showSnap('');
  }
  function updateGhost(ray) {
    const shown = state.shown;
    if (!shown.boxes[`${state.add?.part}.dat`] && state.add) return;
    if (!state.add) { clearGhost(); return; }
    const ref = `${state.add.part.replace(/\.dat$/i, '').toLowerCase()}.dat`;
    const box = shown.boxes[ref] || FALLBACK_BOX;
    const placement = Core.placementFor(ray, shown.leaves, shown.boxes, box, groundY(), rootSection());
    if (shown.ghost) shown.scene.scene.remove(shown.ghost);
    shown.ghost = null;
    ghostPlacement = placement;
    if (placement) {
      shown.ghost = outline(box, [1, 0, 0, 0, 1, 0, 0, 0, 1], placement.world, HOVER_COLOUR, true);
      shown.scene.scene.add(shown.ghost);
      showSnap(placement.to ? `Snaps to ${placement.to}` : 'Drops on the ground, snaps to a stud if it can');
    } else showSnap('');
    shown.scene.render();
  }
  const rootSection = () => (state.session && state.session.plan && state.session.plan.sections[0].name) || 'main.ldr';

  // The parts follow the pointer during a drag (and the outline, as before); nothing is sent until release.
  function previewDrag(d) {
    const shown = state.shown, boxes = shown.selectionBoxes;
    const edits = dragEdits(d);
    if (edits && shown.placements.length <= LIVE_LIMIT) paint(edits.length ? Core.predictEdits(state.nodes, edits) : state.nodes, true);
    if (!boxes) return;
    const a = sceneAxis(d.axis);
    const pivot = new THREE.Vector3().fromArray(Core.ldrawToScene(d.pivot, shown.offset.toArray()));
    if (d.kind === 'move') boxes.matrix.makeTranslation(a.x * d.amount, a.y * d.amount, a.z * d.amount);
    else {
      boxes.matrix.makeTranslation(pivot.x, pivot.y, pivot.z)
        .multiply(new THREE.Matrix4().makeRotationAxis(a, d.amount * Math.PI / 180))
        .multiply(new THREE.Matrix4().makeTranslation(-pivot.x, -pivot.y, -pivot.z));
    }
    boxes.matrixWorldNeedsUpdate = true;
    shown.scene.render();
  }

  // The edits a drag stands for now, or null when they cannot be made (a repeated part cannot be rotated).
  function dragEdits(d) {
    if (!d.amount) return [];
    const ids = state.selected.filter(key => state.editable.has(key));
    try {
      return d.kind === 'move'
        ? Core.moveEdits(state.shown.placements, ids, Core.AXES[d.axis].map(v => v * d.amount))
        : Core.rotateEdits(state.shown.placements, ids, d.axis, d.amount);
    } catch (error) { return null; }
  }

  function endGesture(e, cancelled) {
    const g = state.gesture, shown = state.shown;
    state.gesture = null;
    marquee.hidden = true;
    showSnap('');
    if (!g || !shown) return;
    if (g.drag || g.box) shown.scene.orbitControls.enabled = true;
    if (g.box) return finishBox(g, cancelled);
    // A click on a gizmo handle that did not move anything is a click on the part behind it (the gizmo sits on the
    // selection, so otherwise a second click there could never step to the next overlapping part).
    const plainClick = g.drag && !cancelled && !g.drag.amount && Math.hypot(e.clientX - g.x, e.clientY - g.y) <= CLICK_PIXELS;
    if (g.drag && !plainClick) {
      const d = g.drag;
      const ids = state.selected.filter(key => state.editable.has(key));
      let edits = [];
      try {
        edits = cancelled || !d.amount ? [] :
          d.kind === 'move'
            ? Core.moveEdits(shown.placements, ids, Core.AXES[d.axis].map(v => v * d.amount))
            : Core.rotateEdits(shown.placements, ids, d.axis, d.amount);
      } catch (error) {
        paint(state.nodes);
        drawOverlay();
        setStatus(`${error.message} (the model is unchanged)`, true);
        post({ type: 'obd:error', message: error.message });
        return;
      }
      if (!edits.length) { paint(state.nodes); drawOverlay(); setRestingStatus(); return; }
      send(edits);                                                        // one drag, one request
      return;
    }
    // Not a gizmo drag: a click (little movement) acts; a longer drag was an orbit.
    if (cancelled || Math.hypot(e.clientX - g.x, e.clientY - g.y) > CLICK_PIXELS) return;
    click(e, g);
  }
  stage.addEventListener('pointerup', e => endGesture(e, false));
  stage.addEventListener('pointercancel', e => endGesture(e, true));
  addEventListener('blur', () => { if (state.gesture) endGesture({ clientX: 0, clientY: 0 }, true); });

  function finishBox(g, cancelled) {
    const shown = state.shown, b = g.box;
    if (cancelled || Math.hypot(b.x1 - b.x0, b.y1 - b.y0) <= CLICK_PIXELS) return;       // a click with Ctrl held: nothing to select
    const rect = shown.scene.renderer.domElement.getBoundingClientRect();
    const toNdc = (x, y) => [(x - rect.left) / rect.width * 2 - 1, 1 - (y - rect.top) / rect.height * 2];
    const [x0, y0] = toNdc(b.x0, b.y0), [x1, y1] = toNdc(b.x1, b.y1);
    const found = Core.boxSelect({ x0, y0, x1, y1 }, shown.leaves, shown.boxes, projector());
    setSelected(Core.mergeSelection(state.selected, found, b.add));
    state.lastClick = null;
    setStatus(`${found.length} part${found.length === 1 ? '' : 's'} in the rectangle${state.selected.length !== found.length ? `, ${state.selected.length} selected` : ''}`, false);
  }

  function setSelected(keys) {
    state.selected = keys;
    drawOverlay();
    refreshControls();
    setRestingStatus();
  }

  // A click without a drag, by tool.
  function click(e, g) {
    const shown = state.shown;
    const ray = pointerRay(e).ldraw;
    if (state.tool === 'paint' || state.tool === 'delete') {
      if (!editing()) return;
      const hit = Core.pickHits(ray, shown.leaves, shown.boxes)[0];
      if (!hit) return;
      if (!hit.p.editable) { setStatus(`${hit.p.id} is not an entry of the plan, so it cannot be edited here.`, true); return; }
      if (state.tool === 'paint') paintParts([hit.p.key]);
      else removeParts([hit.p.key]);
      return;
    }
    if (state.tool === 'add') {
      if (!editing()) return;
      placePart(ray);
      return;
    }
    // Overlapping boxes: a second click on the same spot steps to the next part under the pointer.
    const hits = Core.pickAll(ray, shown.leaves, shown.boxes);
    const last = state.lastClick;
    const again = last && last.session === shown && Math.hypot(e.clientX - last.x, e.clientY - last.y) <= CLICK_PIXELS;
    const hit = Core.nextHit(hits, again && !(g.shift || e.shiftKey) ? last.key : undefined);
    state.lastClick = hit ? { x: e.clientX, y: e.clientY, key: hit.key, session: shown } : null;
    setSelected(Core.nextSelection(state.selected, hit ? hit.key : null, g.shift || e.shiftKey));
    if (hit && !hit.editable && !state.readonly) {
      setStatus(`${hit.id} is not an entry of the plan, so it cannot be edited here.`, true);
    } else if (hits.length > 1) {
      setStatus(`${status.textContent} · ${hits.indexOf(hit) + 1} of ${hits.length} under the pointer, click again for the next`, false);
    }
  }

  function setRestingStatus() {
    const s = state.session;
    if (!s) return;
    const picked = state.selected.length ? ` · ${state.selected.length} selected` : '';
    const note = state.shown ? Core.describeSelection(state.shown.placements, state.selected) : '';
    if (embed) {
      const count = state.shown ? state.shown.leaves.length : 0;
      const where = state.readonly ? ' · saved version, read-only' : state.tool === 'add' && state.add ? ` · adding ${state.add.part}` : state.tool === 'paint' ? ' · paint' : '';
      setStatus(`${count.toLocaleString()} part${count === 1 ? '' : 's'}${picked}${where}${note ? ` · ${note}` : ''}`, false);
      return;
    }
    setStatus(`Revision ${s.revision} · ${Core.describeParts(s.parts)}${picked}${note ? ` · ${note}` : ''}`, false);
  }

  // ---- sending edits --------------------------------------------------------------

  // Edits go out one at a time, in order. Each is shown at once (when it can be predicted) and confirmed by the
  // server's answer, which is applied as a difference. The first failure rolls the picture back to the last answer and
  // drops what was queued behind it (those edits were made on top of the one that failed).
  const queue = [];
  let pumping = false;

  // Show what the edits will give, then send them. `retry` runs when the server refuses (400) the first form.
  function send(edits, { optimistic = true, retry, predicted: given, label } = {}) {
    const entry = { kind: edits.map(e => e.op).join('+'), edits, t0: performance.now(), label };
    if ((optimistic || given) && state.shown) {
      try {
        const predicted = given || Core.predictEdits(state.nodes, edits);
        indexNodes(state.shown, predicted);
        state.selected = state.selected.filter(key => state.shown.byKey.has(key));
        paint(predicted, true);
        drawOverlay();
      } catch (e) { /* not predictable: the answer will show it */ }
    }
    entry.optimisticMs = performance.now() - entry.t0;
    enqueue({ entry, run: () => call('/edits', { ...Core.editsBody(edits, state.session.revision), delta: true }), retry });
  }

  function enqueue(job) {
    if (!job.entry) job.entry = { kind: job.label, t0: performance.now(), optimisticMs: 0 };
    queue.push(job);
    pump();
  }

  // The server answers an edit with the difference (`delta`) or, for anything it cannot describe so, the whole model.
  // A difference is applied to the last confirmed plan and picture; when that cannot be done the whole model is fetched.
  async function resolveReply(reply) {
    if (!reply.delta) return { session: reply, nodes: null };
    const base = state.confirmed;
    const merged = base && Core.applyDelta(base.session.plan, base.nodes, reply.splices);
    if (merged) {
      const { splices, delta, ...rest } = reply;
      return { session: { ...base.session, ...rest, plan: merged.plan, mpd: null }, nodes: merged.nodes };
    }
    return { session: await call(''), nodes: null };
  }

  async function pump() {
    let failed = null;
    if (pumping) return;
    pumping = true;
    state.busy = true;
    refreshControls();
    setStatus('Saving…', false);
    try {
      while (queue.length) {
        const job = queue.shift();
        failed = job.entry;
        const sent = performance.now();
        let reply;
        try { reply = await job.run(); }
        catch (e) {
          if (job.retry && e.status === 400) reply = await job.retry();
          else throw e;
        }
        const requestMs = performance.now() - sent;
        const { session, nodes } = await resolveReply(reply);
        state.session = session;                               // revision and depths: the next queued edit builds on them
        let applyMs = 0;
        if (!queue.length) applyMs = await applyConfirmed(session, false, nodes) || 0;
        else state.confirmed = { session, nodes: nodes || Core.flatten(session.mpd, session.plan).nodes };
        job.entry.requestMs = requestMs; job.entry.applyMs = applyMs; job.entry.totalMs = performance.now() - job.entry.t0;
        state.perf.push(job.entry);
        post({ type: 'obd:changed', revision: session.revision, undo_depth: session.undo_depth, redo_depth: session.redo_depth });
      }
    } catch (e) {
      queue.length = 0;
      if (e.status === 409) {
        // Someone else changed the plan: take the server's picture and say so.
        try { await applyConfirmed(await call('')); } catch (again) { /* the next poll tries again */ }
        fail(`${e.message}. Reloaded the current model.`);
      } else {
        await rollback().catch(() => {});
        fail(`${failed && failed.label ? `Could not ${failed.label}: ` : ''}${e.message} (the model is unchanged)`);
      }
    } finally {
      pumping = false;
      state.busy = false;
      drawOverlay();
      refreshControls();
      if (!status.classList.contains('error')) setRestingStatus();
    }
  }

  const needEdit = what => {
    if (editing()) return true;
    fail(state.readonly ? 'A saved version is on screen: it is read-only. Go back to the draft to edit.' : state.mode !== 'edit' ? `Switch to Edit mode to ${what}.` : `Cannot ${what} now.`);
    return false;
  };

  const undo = () => state.session && state.session.undo_depth > 0 && needEdit('undo') &&
    enqueue({ label: 'undo', run: () => call('/undo', { base_revision: state.session.revision, delta: true }) });
  const redo = () => state.session && state.session.redo_depth > 0 && needEdit('redo') &&
    enqueue({ label: 'redo', run: () => call('/redo', { base_revision: state.session.revision, delta: true }) });

  // Delete these placements (by key): the sub-model a lone part sits in goes with it, as before.
  function removeParts(keys) {
    const editable = keys.filter(key => state.editable.has(key));
    const targets = Core.deleteTargets(state.shown.placements, editable, state.session.plan);
    const edits = Core.deleteEdits(state.shown.placements, targets);
    if (!edits.length) return;
    state.selected = state.selected.filter(k => !targets.includes(k));
    send(edits);
  }
  const remove = () => needEdit('delete') && removeParts(state.selected);

  function paintParts(keys, colour = state.colour) {
    const edits = Core.recolourEdits(state.shown.placements, keys.filter(key => state.editable.has(key)), colour);
    const changing = edits.filter(edit => state.shown.placements.some(p => p.entryId === edit.id && p.section === edit.section && String(p.colour) !== String(colour)));
    if (changing.length) send(changing);
  }

  // The add tool: put the armed part where the pointer is.
  function placePart(ray) {
    if (!state.add) { setStatus('Choose a part first (the Parts list), then click where it goes.', false); return; }
    const shown = state.shown;
    const ref = `${state.add.part.replace(/\.dat$/i, '').toLowerCase()}.dat`;
    const placement = Core.placementFor(ray, shown.leaves, shown.boxes, shown.boxes[ref] || FALLBACK_BOX, groundY(), rootSection());
    if (!placement) { setStatus('Nothing to place it on there: click on the model or the ground.', false); return; }
    const plan = state.session.plan;
    const id = Core.nextEntryId(plan, state.nodes);           // what the server will call it (adds still queued count)
    const withSnap = Core.addEdits(plan, state.add.part, state.add.colour, placement, true, id);
    const without = Core.addEdits(plan, state.add.part, state.add.colour, placement, false, id);
    // The part shows where it was dropped at once (outlined until the server answers); the answer slides it onto its
    // snap or, when the add is refused, takes it away again with the reason.
    let predicted = null;
    if (!shown.leafScene.missing([{ ref: without[0].part }]).length) {
      predicted = Core.optimisticAdd(state.nodes, plan, id, placement.section, state.add.part, state.add.colour, placement.local);
    }
    // One batch (add + snap), so one undo removes the part. If the snap is refused (nothing to snap to) the part is
    // added where it was dropped; an add that is refused itself (inventory) fails the same way again.
    send(withSnap, {
      optimistic: false,
      predicted,
      label: `add ${state.add.part}`,
      retry: () => call('/edits', { ...Core.editsBody(without, state.session.revision), delta: true }),
    });
  }

  buttons.undo.addEventListener('click', undo);
  buttons.redo.addEventListener('click', redo);
  buttons.del.addEventListener('click', remove);
  // One level up: the part becomes the sub-model it sits in (then gizmo drags move the whole group).
  const widen = () => {
    if (!state.shown || buttons.widen.disabled) return;
    setSelected(Core.widenSelection(state.shown.placements, state.selected));
  };
  buttons.widen.addEventListener('click', widen);
  buttons.home.addEventListener('click', () => state.shown && resetView(state.shown));
  buttons.publish.addEventListener('click', async () => {
    state.busy = true;
    refreshControls();
    setStatus('Publishing…', false);
    try {
      const result = await call('/publish', {});
      // The chat now has a message and a card for this publish; say what was published and link to it.
      const check = result.parts_check;
      const checkText = check ? ` Parts check: ${check.complete ? 'all parts owned' : check.summary}` : '';
      if (buildId) {
        setStatus(`${result.message || `Saved as v${result.version || '?'} in My Builds`}, ${result.pieces || '?'} piece${result.pieces === 1 ? '' : 's'} (${result.file}). It is marked "edited by you".`, false);
        chatLink.textContent = `View v${result.version || ''} in the build`;
        return;
      }
      setStatus(`Published as v${result.version || '?'}, ${result.pieces || '?'} piece${result.pieces === 1 ? '' : 's'}.${checkText} It is in the chat as "edited by you".`, false);
      if (result.chat_url) { chatLink.href = result.chat_url; chatLink.textContent = `View v${result.version || ''} in the chat`; }
    } catch (e) { setStatus(`Could not publish: ${e.message}`, true); }
    finally { state.busy = false; refreshControls(); }
  });

  function setTool(tool) {
    const changed = tool !== state.tool;
    state.tool = tool;
    if (changed) post({ type: 'obd:tool', tool });
    if (tool !== 'add') clearGhost();
    state.lastClick = null;
    drawOverlay();
    refreshControls();
    setRestingStatus();
  }
  toolButtons.forEach(b => b.addEventListener('click', () => setTool(b.dataset.tool)));

  // ---- keyboard -------------------------------------------------------------------------------
  // V M R B P: tools. Delete: delete. Ctrl+Z, Ctrl+Shift+Z (or Ctrl+Y): undo, redo. F: frame the selection (or the
  // model), Home: reset the view. G: widen to the sub-model. Arrows: nudge. Esc: clear (or leave the add tool).
  // Space, Left, Right in the player. The stage takes focus on click.
  const TOOL_KEYS = { v: 'select', m: 'move', r: 'rotate', b: 'add', p: 'paint' };
  addEventListener('keydown', e => {
    if (!state.shown || (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName))) return;
    const mod = e.ctrlKey || e.metaKey;
    const key = e.key.toLowerCase();
    if (mod && key === 's' && !e.shiftKey) { e.preventDefault(); post({ type: 'obd:shortcut', action: 'save' }); return; }
    if (state.mode === 'player') return playerKey(e);
    if (state.mode === 'view') { if (!mod && (key === 'f' || e.key === 'Home')) { e.preventDefault(); key === 'f' ? frameSelection() : resetView(state.shown); } return; }
    if (mod && key === 'z') { e.preventDefault(); (e.shiftKey ? redo : undo)(); }
    else if (mod && key === 'y') { e.preventDefault(); redo(); }
    else if (mod) return;
    else if (e.altKey) return;
    else if (TOOL_KEYS[key] && editing()) { e.preventDefault(); setTool(TOOL_KEYS[key]); }
    else if ((e.key === 'Delete' || e.key === 'Backspace') && state.selected.length) { e.preventDefault(); remove(); }
    else if (key === 'f') { e.preventDefault(); frameSelection(); }
    else if (e.key === 'Home') { e.preventDefault(); resetView(state.shown); }
    else if (key === 'g' && state.selected.length) widen();
    else if (/^(Arrow(Left|Right|Up|Down)|Page(Up|Down))$/.test(e.key) && state.selected.length && editing()) {
      e.preventDefault();                        // nudge: one stud / plate per press, one undo step each
      let edits = [];
      try { edits = Core.nudgeEdits(state.shown.placements, state.selected.filter(k => state.editable.has(k)), e.key, e.shiftKey); }
      catch (error) { setStatus(`${error.message} (the model is unchanged)`, true); return; }
      if (edits.length) send(edits);
    }
    else if (e.key === 'Escape') {
      if (state.tool === 'add' || state.tool === 'paint' || state.tool === 'delete') setTool('select');
      else if (state.selected.length) setSelected([]);
    }
  });

  // ---- live refresh: pick up changes made by the agent, the palette or another tab ------

  let refreshing = false;
  const idle = () => !state.busy && !state.stale && !state.gesture && !refreshing && state.session && !state.readonly && !queue.length;
  // `force`: apply the server's picture even when the revision is the one on screen (the backend changed the plan some
  // other way: a regenerate, a discard). The difference is empty when nothing changed, so this is cheap.
  async function refresh(reframe, force) {
    if (!idle()) return;
    refreshing = true;                           // a request in flight: no second poll
    try {
      const session = await call('');
      if ((!force && session.revision === state.session.revision) || state.busy || state.gesture || state.readonly) return;
      state.busy = true;
      refreshControls();
      try { await applyConfirmed(session, reframe); } finally { state.busy = false; refreshControls(); }
    } catch (e) { /* offline or no plan yet: try again at the next tick */ }
    finally { refreshing = false; }
  }
  // Embedded, the parent says when to look (reload); on its own the page polls.
  if (!embed) setInterval(() => { if (!document.hidden) refresh(); }, 3000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });

  // ---- viewing a saved version (read-only) ------------------------------------------------------

  const MAX_VERSION_BYTES = 40 * 1024 * 1024;
  let parked = null;          // the draft's scene while a version is on screen: { shown, session, confirmed, selected }

  async function viewVersion(url) {
    let text;
    try {
      const response = await fetch(url, { credentials: 'same-origin' });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`.trim());
      text = await response.text();
      if (text.length > MAX_VERSION_BYTES) throw new Error('the file is too large');
    } catch (e) { fail(`Could not open that version: ${e.message}`); return; }
    const draft = parked || { shown: state.shown, session: state.session, confirmed: state.confirmed, selected: state.selected, nodes: state.nodes };
    const viewed = state.shown;
    const session = { mpd: text, plan: null, revision: null, undo_depth: 0, redo_depth: 0, name: 'saved version', parts: null };
    state.busy = true;
    refreshControls();
    try {
      await new Promise(resolve => { queue.length = 0; resolve(); });
      const ok = await showFull(session, { reframe: false, keepOld: !parked });
      if (!ok) return;
      if (parked && viewed) retire(viewed);                    // a version replaced by another version
      parked = draft;
      state.readonly = true;
      state.session = session;
      state.confirmed = null;
      lastSelection = '';
      afterShow();
    } finally { state.busy = false; refreshControls(); }
  }

  async function backToDraft() {
    if (!parked) return;
    const draft = parked;
    parked = null;
    retire(state.shown);
    draft.shown.pane.style.display = '';
    state.shown = draft.shown;
    state.session = draft.session;
    state.confirmed = draft.confirmed;
    state.readonly = false;
    indexNodes(draft.shown, draft.nodes);
    state.selected = draft.selected.filter(k => draft.shown.byKey.has(k));
    draft.shown.scene.onChange();
    lastSelection = '';
    afterShow();
    refresh(false, true);                                      // the draft may have changed meanwhile
  }

  // ---- modes ---------------------------------------------------------------------------------

  let playerInstance = null, playerStarting = null, pendingStep = null;
  const playerSource = () => ({ mpd: state.session.mpd, key: state.readonly ? `version:${state.session.mpd.length}:${state.session.mpd.slice(0, 200)}` : `draft:${state.session.revision}` });

  function playerKey(e) {
    if (!playerInstance || e.metaKey || e.ctrlKey || e.altKey) return;
    const actions = { ' ': () => playerInstance.toggle(), ArrowRight: () => playerInstance.next(), ArrowLeft: () => playerInstance.previous(),
                      Home: () => playerInstance.showStep(1), End: () => playerInstance.showStep(playerInstance.stepCount) };
    const action = actions[e.key];
    if (!action) return;
    e.preventDefault();
    if (e.target instanceof HTMLButtonElement) e.target.blur();
    action();
  }

  let lastSteps = '';
  function postSteps() {
    const p = playerInstance;
    if (!p || state.mode !== 'player') return;
    const sig = `${p.stepCount}:${p.step}`;
    if (sig === lastSteps) return;
    lastSteps = sig;
    post({ type: 'obd:steps', total: p.stepCount, current: p.step });
  }

  function updatePlayerBar() {
    const p = playerInstance;
    if (!p) return;
    $('player-play').innerHTML = p.playing ? '&#10074;&#10074;' : '&#9654;';
    $('player-play').setAttribute('aria-label', p.playing ? 'Pause' : 'Play');
    $('player-step').textContent = p.stepCount ? `Step ${p.step} / ${p.stepCount}` : 'Step - / -';
    postSteps();
    if (document.activeElement !== $('player-scrub')) $('player-scrub').value = String(p.duration ? Math.round(p.time / p.duration * 1000) : 0);
  }

  async function enterPlayer() {
    if (!state.session) return;
    if (state.session.mpd == null) {                           // an edit was answered with a difference: fetch the model's text
      try {
        const full = await call('');
        if (full.revision === state.session.revision) state.session.mpd = full.mpd; else await applyConfirmed(full);
      } catch (e) { fail(`Could not load the model for the player (${e.message})`); return; }
    }
    $('player-stage').hidden = false;
    stage.style.visibility = 'hidden';
    try {
      if (!playerInstance) {
        playerStarting ||= EditorPlayer.create($('player-canvas'), ViewerCommon.stageCss, params.get('backend')).then(p => {
          playerInstance = p;
          p.onChange(updatePlayerBar);
          return p;
        });
        setStatus('Starting the player…', false);
        await playerStarting;
      }
      if (state.mode !== 'player') return;
      const source = playerSource();
      if (playerInstance.source !== source.key) {
        setStatus('Preparing the player…', false);
        const result = await playerInstance.load(source.mpd, source.key, (stage, done, total) =>
          setStatus(stage === 'parts' ? `Loading parts… ${done.toLocaleString()} files` : `Preparing parts… ${done} / ${total}`, false));
        if (!result) return;
      }
      if (state.mode !== 'player') return;
      if (pendingStep !== null) { playerInstance.showStep(pendingStep); pendingStep = null; }
      const count = playerInstance.stepCount;
      setStatus(`${count} step${count === 1 ? '' : 's'} · Space plays, Left and Right step`, false);
      lastSteps = '';
      updatePlayerBar();
    } catch (e) {
      playerStarting = null;
      fail(`Could not start the 3D player: ${e && e.message || e}`);
      await setMode('edit');
    }
  }

  function leavePlayer() {
    $('player-stage').hidden = true;
    stage.style.visibility = '';
    if (playerInstance) playerInstance.pause();
    if (state.shown) state.shown.scene.onChange();
  }

  async function setMode(mode, quiet) {
    if (mode === state.mode && quiet !== 'init') return;
    const before = state.mode;
    state.mode = mode;
    document.body.classList.toggle('mode-view', mode === 'view');
    document.body.classList.toggle('mode-player', mode === 'player');
    lastSteps = '';
    if (mode === 'player') await enterPlayer();
    else if (before === 'player') leavePlayer();
    clearGhost();
    state.hover = null;
    if (state.shown) drawOverlay();
    refreshControls();
    if (mode !== 'player') setRestingStatus();
    if (!quiet) post({ type: 'obd:mode', mode });
  }

  $('player-play').addEventListener('click', () => playerInstance && playerInstance.toggle());
  $('player-next').addEventListener('click', () => playerInstance && playerInstance.next());
  $('player-prev').addEventListener('click', () => playerInstance && playerInstance.previous());
  $('player-scrub').addEventListener('input', e => playerInstance && playerInstance.seekFraction(Number(e.target.value) / 1000));

  // ---- messages from the page that embeds this one -------------------------------------------------

  function handleCommand(data) {
    let command;
    try { command = Core.parseCommand(data, location.origin, location.href); }
    catch (e) { fail(e.message); return; }
    switch (command.cmd) {
      case 'undo': undo(); break;
      case 'redo': redo(); break;
      case 'delete': if (needEdit('delete') && state.selected.length) remove(); break;
      case 'set-tool':
        if (!needEdit('use a tool')) break;
        setTool(command.tool);
        break;
      case 'set-colour':
        state.colour = command.colour;
        if (state.add) state.add.colour = command.colour;
        if (state.selected.length && editing()) paintParts(state.selected, command.colour);
        break;
      case 'add-part': {
        if (!needEdit('add a part')) break;
        state.add = { part: command.part.replace(/\.dat$/i, '').toLowerCase(), colour: command.colour };
        state.colour = command.colour;
        setTool('add');
        armPart(state.add.part);
        break;
      }
      case 'set-mode': setMode(command.mode); break;
      case 'reload': if (state.readonly) { if (parked) parked.stale = true; } else refresh(false, true); break;
      case 'view-version': viewVersion(command.url); break;
      case 'back-to-draft': backToDraft(); break;
      case 'focus': if (state.shown) resetView(state.shown); break;
      case 'step':
        if (state.mode === 'player' && playerInstance) playerInstance.showStep(command.n);
        else pendingStep = command.n;
        break;
    }
  }

  // Load the armed part's file ahead of the first click, and learn its box for the ghost.
  async function armPart(part) {
    const shown = state.shown;
    if (!shown) return;
    const ref = `${part}.dat`;
    if (shown.leafScene.missing([{ ref }]).length) await ensureParts(shown, [ref]);
    if (state.shown !== shown) return;
    addBoxes(shown, [{ ref, leaf: true }]);
    setRestingStatus();
  }

  addEventListener('message', e => {
    if (!Core.trustedMessage(e, location.origin, window.parent)) return;
    const d = e.data;
    if (d.type === 'obd:command') handleCommand(d);
    else if (d.type === 'reload') refresh(true, true);   // the palette added a part: frame the whole model
    else if (d.type === 'ping') postState();
  });

  addEventListener('resize', () => state.shown && state.shown.scene.onChange());
  addEventListener('pagehide', event => { if (!event.persisted) state.shown?.scene.dispose?.(); });

  // ---- start ------------------------------------------------------------------------------

  // The start panel: the plans the chat's folder holds (what the agent built) to open, or a new one-brick model.
  // With a model already open ("Open plan...") the choice replaces it; the old plan is kept aside by the server.
  async function showStart(replace) {
    const list = $('plans');
    list.textContent = '';
    $('start').hidden = false;
    $('start-cancel').hidden = !replace;
    $('start-button').hidden = Boolean(replace);
    $('start-text').textContent = replace
      ? 'Open another plan from this chat. The model you are editing is put aside in the chat folder, not deleted.'
      : 'This chat has no model to edit yet.';
    let plans = [];
    try { plans = (await call('/plans')).plans; } catch (e) { /* the list is a convenience: starting a new model still works */ }
    if (!replace && plans.length) $('start-text').textContent = 'Which model do you want to edit?';
    else if (replace && !plans.length) $('start-text').textContent = 'This chat has no other plan files to open.';
    for (const plan of plans) {
      const li = document.createElement('li'), button = document.createElement('button');
      const small = document.createElement('small');
      button.textContent = plan.path;
      small.textContent = `${plan.entries} placements · ${plan.sections} section${plan.sections === 1 ? '' : 's'} · ${plan.title}`;
      button.appendChild(small);
      button.addEventListener('click', () => openPlan(plan, replace));
      li.appendChild(button);
      list.appendChild(li);
    }
  }

  async function openPlan(plan, replace) {
    $('plans').querySelectorAll('button').forEach(b => { b.disabled = true; });
    setStatus(`Opening ${plan.path}...`, false);
    try {
      const name = plan.title.split(/ [-–—] /)[0].trim().slice(0, 60) || plan.path;
      const session = await call('', { source: plan.path, name, replace: Boolean(replace) });
      $('start').hidden = true;
      state.selected = [];
      await applyConfirmed(session, true);
      $('open-other').disabled = false;
    } catch (e) {
      setStatus(`Could not open ${plan.path}: ${e.message}`, true);
      $('plans').querySelectorAll('button').forEach(b => { b.disabled = false; });
    }
  }
  $('open-other').addEventListener('click', () => showStart(true));
  $('start-cancel').addEventListener('click', () => { $('start').hidden = true; setRestingStatus(); });

  async function begin() {
    document.body.classList.toggle('mode-view', state.mode === 'view');
    document.body.classList.toggle('mode-player', state.mode === 'player');
    try {
      const opened = await call('');
      if (await applyConfirmed(opened, true) === false) return;
      $('open-other').disabled = false;
      if (opened.warnings && opened.warnings.length) {
        setStatus(`${opened.warnings.length} line${opened.warnings.length === 1 ? '' : 's'} of the model could not be imported and are left out: ${opened.warnings.slice(0, 3).join('; ')}`, true);
      }
      if (state.shown) {
        if (state.mode === 'player') { await enterPlayer(); }
        post({ type: 'obd:ready', mode: state.mode });
      }
    } catch (e) {
      if (e.status === 404 && /no editor plan/i.test(e.message) && !embed) {
        await showStart(false);
        setStatus('No editor plan for this chat yet.', false);
      } else fail(`Could not open the editor: ${e.message}`);
    }
  }
  $('start-button').addEventListener('click', async () => {
    $('start-button').disabled = true;
    try {
      const session = await call('', {});
      $('start').hidden = true;
      await applyConfirmed(session, true);
      $('open-other').disabled = false;
    } catch (e) { setStatus(`Could not start a model: ${e.message}`, true); }
    finally { $('start-button').disabled = false; }
  });
  begin();
})();
