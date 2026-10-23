// The editor page. The maths and the request building live in editor-core.js
// (tested under node); this file is the three.js and DOM glue around it.
//
// Flow: GET the editor session -> show its MPD. A click picks a part (the ray is
// tested against each part's own bounding box, see EditorCore.pick). A drag on a
// gizmo handle previews locally and, on release, sends ONE edits request. The
// backend rebuilds and returns the new MPD; only then is the old render replaced,
// so a failed edit leaves the last good picture on screen.
//
// Scene bookkeeping: ldbi flips Y and Z and recentres the model, so scene
// coordinates differ from LDraw's. `offset` is that recentring; EditorCore's
// sceneToLdraw / ldrawToScene convert.
(function () {
  const { params, statusSetter } = ViewerCommon;
  const Core = EditorCore;
  const LIB = '/ldraw';
  const chatId = params.get('chat');
  const stage = document.getElementById('stage');
  const status = document.getElementById('status');
  const setStatus = statusSetter(status);
  const $ = id => document.getElementById(id);
  const buttons = { undo: $('undo'), redo: $('redo'), del: $('delete'), publish: $('publish'), home: $('home') };
  const toolButtons = document.querySelectorAll('button[data-tool]');

  if (!chatId) { setStatus('No chat given: add ?chat=<chat id>', true); return; }
  if (!WEBGL.isWebGLAvailable()) { setStatus('WebGL is not available in this browser.', true); return; }

  const narrow = matchMedia('(max-width: 800px), (pointer: coarse)');
  const canEdit = () => !narrow.matches;
  function showDeviceNote() {
    document.body.classList.toggle('readonly', !canEdit());
    $('desktop-note').hidden = canEdit();
  }
  narrow.addEventListener('change', showDeviceNote);
  showDeviceNote();

  // ---- API ----------------------------------------------------------------

  async function call(path, body, method) {
    const response = await fetch(`/api/chats/${encodeURIComponent(chatId)}/editor${path}`, {
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

  LDR.Options.bgColor = 0xF2F2F2;
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

  // ---- state ----------------------------------------------------------------

  const state = {
    session: null,        // the last response of the editor API
    shown: null,          // what is on screen: { scene, pane, offset, controls, camera, placements, boxes, radius, center }
    selected: [],
    editable: new Set(),  // ids of the plan's root entries
    tool: 'move',
    busy: false,
    stale: false,         // the last rebuild could not be drawn: block edits until the page is reloaded
    gesture: null,        // { down, drag }
  };

  // ---- loading a scene --------------------------------------------------------

  function loadScene(mpd) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(new Blob([mpd], { type: 'text/plain' }));
      const pane = document.createElement('div');
      pane.className = 'pane';
      pane.style.visibility = 'hidden';                // shown once built, so a failed build never flashes
      const canvas = document.createElement('canvas');
      pane.appendChild(canvas);
      stage.appendChild(pane);
      const idToUrl = id => (id === url ? [url] : [`/ldraw-id/${id}`]);
      const missing = new Set();
      let scene;
      const fail = message => {
        clearTimeout(timer);
        URL.revokeObjectURL(url);
        try { scene && scene.dispose && scene.dispose(); } catch (e) { /* ignore */ }
        pane.remove();
        reject(new Error(message));
      };
      // A load that never reports back (a throwing callback in the vendored scene code) must not leave the page stuck.
      const timer = setTimeout(() => fail('Loading the model timed out'), 30000);
      LDR.Colors.load(() => {
        try {
          scene = new LDrawOrg.Model(canvas, url, {
            idToUrl, idToTextureUrl: id => `${LIB}/parts/textures/${id.toLowerCase()}`,
            onProgress: () => {},
            onLoaded: () => { clearTimeout(timer); URL.revokeObjectURL(url); resolve({ scene, pane, missing }); },
            physicalRenderingAge: 0,
          });
          window.scene = scene;                       // ldraworgscene.js refers to a global `scene` (viewer.js declares it too)
          scene.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
          scene.renderer.shadowMap.enabled = false;
          usePerspective(scene);
          scene.loader.onError = e => {
            if (e && e.subModel === url) fail('The model could not be loaded');
            else if (e && e.subModel) missing.add(e.subModel);
          };
          scene.loader.onWarning = () => {};
        } catch (e) { fail(String(e.message || e)); }
      }, () => fail('Could not load LDConfig.ldr (colour definitions).'), `${LIB}/LDConfig.ldr`);
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
      if (this.mc) {
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
    if (box.isEmpty()) return;
    box.getCenter(shown.center);
    shown.radius = Math.max(10, box.getSize(new THREE.Vector3()).length() / 2);
    const camera = scene.camera, controls = scene.orbitControls;
    if (previous) {
      camera.position.fromArray(Core.ldrawToScene(previous.position, shown.offset.toArray()));
      controls.target.fromArray(Core.ldrawToScene(previous.target, shown.offset.toArray()));
    } else resetView(shown);
    camera.lookAt(controls.target);
    controls.update();
    scene.render();
  }

  function resetView(shown) {
    const camera = shown.scene.camera, controls = shown.scene.orbitControls;
    const distance = Core.fitDistance(shown.radius, FOV, camera.aspect, 0.33);
    camera.position.copy(shown.center).addScaledVector(new THREE.Vector3(1, 0.7, 1).normalize(), distance);
    controls.target.copy(shown.center);
    camera.lookAt(shown.center);
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
    if (own && !own.isEmpty()) return own.clone();
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

  function boxesFor(scene, placements) {
    const boxes = {};
    for (const p of placements) {
      if (boxes[p.ref]) continue;
      let box = null;
      try { box = partBox(scene.loader, p.ref); } catch (e) { box = null; }
      boxes[p.ref] = box ? { min: box.min.toArray(), max: box.max.toArray() } : FALLBACK_BOX;
    }
    return boxes;
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
    try { shown.scene.dispose && shown.scene.dispose(); } catch (e) { /* ignore */ }
    try { shown.scene.renderer.dispose(); shown.scene.renderer.forceContextLoss(); } catch (e) { /* ignore */ }
  }

  // Draw `session.mpd`; the previous picture stays until the new one is built.
  async function show(session) {
    const old = state.shown;
    const previous = old ? cameraInLdraw(old) : null;
    let built;
    try {
      built = await loadScene(session.mpd);
    } catch (e) {
      state.stale = true;
      state.session = session;
      refreshControls();
      setStatus(`Could not draw the rebuilt model (${e.message}). Showing the last good picture; reload the page to continue.`, true);
      return false;
    }
    const { scene, pane, missing } = built;
    const rootName = session.plan.sections[0].name;
    const placements = Core.parsePlacements(session.mpd, rootName);
    placements.forEach(p => { p.section = rootName; });      // edits name the plan's section, not the FILE line
    const shown = {
      scene, pane, placements, boxes: boxesFor(scene, placements), center: new THREE.Vector3(), radius: 100,
      offset: scene.baseObject.position.clone(),
    };
    scene.onFirstFrame = () => frame(shown, previous);
    pane.style.visibility = 'visible';
    scene.onChange();
    if (old) {
      retire(old);
    }
    state.shown = shown;
    state.session = session;
    state.stale = false;
    state.editable = Core.entryIds(session.plan);
    state.selected = state.selected.filter(id => shown.placements.some(p => p.id === id));
    drawOverlay();
    refreshControls();
    $('file').textContent = session.name || 'manual model';
    const missingNote = missing.size ? ` · ${missing.size} part(s) not found` : '';
    setStatus(`Revision ${session.revision} · ${Core.describeParts(session.parts)}${missingNote}`, false);
    return true;
  }

  function refreshControls() {
    const s = state.session;
    const idle = !state.busy && !state.stale && canEdit();
    buttons.undo.disabled = !(idle && s && s.undo_depth > 0);
    buttons.redo.disabled = !(idle && s && s.redo_depth > 0);
    buttons.del.disabled = !(idle && state.selected.length);
    buttons.publish.disabled = !(idle && s);
    buttons.home.disabled = !state.shown;
    toolButtons.forEach(b => {
      b.setAttribute('aria-pressed', String(b.dataset.tool === state.tool));
      b.disabled = !canEdit();
    });
  }

  // ---- overlay: selection boxes and the gizmo ------------------------------------

  const AXIS_COLOURS = { x: 0xe03131, y: 0x2f9e44, z: 0x1c7ed6 };
  const sceneAxis = axis => { const a = Core.AXES[axis]; return new THREE.Vector3(a[0], -a[1], -a[2]); };   // LDraw axis in scene space

  function drawOverlay() {
    const shown = state.shown;
    if (!shown) return;
    const { scene } = shown;
    if (scene.overlay) { scene.scene.remove(scene.overlay); }
    const overlay = new THREE.Group();
    scene.overlay = overlay;
    shown.selectionBoxes = null;
    shown.gizmo = null;
    const chosen = shown.placements.filter(p => state.selected.includes(p.id));
    if (chosen.length) {
      const boxes = new THREE.Group();
      for (const p of chosen) {
        const box = shown.boxes[p.ref] || FALLBACK_BOX;
        const group = new THREE.Group();
        group.matrixAutoUpdate = false;
        const m = p.matrix, t = Core.ldrawToScene(p.position, shown.offset.toArray());
        group.matrix.set(m[0], m[1], m[2], t[0],
                         -m[3], -m[4], -m[5], t[1],
                         -m[6], -m[7], -m[8], t[2],
                         0, 0, 0, 1);
        const size = [0, 1, 2].map(k => Math.max(box.max[k] - box.min[k], 1));
        const lines = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(size[0], size[1], size[2])),
                                             new THREE.LineBasicMaterial({ color: state.editable.has(p.id) ? 0xff8800 : 0x888888, depthTest: false }));
        lines.position.set((box.min[0] + box.max[0]) / 2, (box.min[1] + box.max[1]) / 2, (box.min[2] + box.max[2]) / 2);
        lines.renderOrder = 998;
        group.add(lines);
        boxes.add(group);
      }
      boxes.matrixAutoUpdate = false;
      overlay.add(boxes);
      shown.selectionBoxes = boxes;
      const pivot = Core.centroid(shown.placements, state.selected.filter(id => state.editable.has(id)));
      if (pivot && state.tool !== 'select' && canEdit()) {
        const gizmo = makeGizmo(state.tool);
        gizmo.position.fromArray(Core.ldrawToScene(pivot, shown.offset.toArray()));
        overlay.add(gizmo);
        shown.gizmo = gizmo;
        shown.pivot = pivot;
      }
    }
    scene.scene.add(overlay);
    scaleGizmo(scene);
    scene.render();
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

  // ---- pointer handling ------------------------------------------------------------

  const CLICK_PIXELS = 4;

  stage.addEventListener('pointerdown', e => {
    stage.focus({ preventScroll: true });
    const shown = state.shown;
    if (!shown || e.button !== 0 || state.busy) return;
    state.gesture = { x: e.clientX, y: e.clientY, shift: e.shiftKey, drag: null };
    if (!canEdit() || state.stale) return;
    const ray = pointerRay(e);
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
    if (!g || !g.drag || !shown) return;
    const d = g.drag, ray = pointerRay(e).ldraw, free = e.altKey;
    if (d.kind === 'move') {
      const s = Core.axisParameter(ray, d.pivot, Core.AXES[d.axis]);
      d.amount = s === null || d.start === null ? 0 : Core.snapMove(d.axis, s - d.start, !free);
    } else {
      const angle = Core.planeAngle(d.startRay, ray, d.pivot, Core.AXES[d.axis]);
      d.amount = angle === null ? 0 : Core.snapRotate(angle, !free);
    }
    previewDrag(d);
    setStatus(d.kind === 'move' ? `Move ${d.axis.toUpperCase()} ${d.amount > 0 ? '+' : ''}${d.amount} LDU (${free ? 'free, Alt held' : `snapping to ${Core.snapStep(d.axis)} LDU, hold Alt for free`})` : `Rotate ${d.axis.toUpperCase()} ${d.amount}° (${free ? 'free, Alt held' : 'snapping to 15°, hold Alt for free'})`, false);
  });

  // The selection's outline follows the pointer; nothing is sent until release.
  function previewDrag(d) {
    const shown = state.shown, boxes = shown.selectionBoxes;
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

  async function endGesture(e, cancelled) {
    const g = state.gesture, shown = state.shown;
    state.gesture = null;
    if (!g || !shown) return;
    if (g.drag) {
      shown.scene.orbitControls.enabled = true;
      const d = g.drag;
      const ids = state.selected.filter(id => state.editable.has(id));
      const edits = cancelled || !d.amount ? [] :
        d.kind === 'move'
          ? Core.moveEdits(shown.placements, ids, Core.AXES[d.axis].map(v => v * d.amount))
          : Core.rotateEdits(shown.placements, ids, d.axis, d.amount);
      if (!edits.length) { drawOverlay(); setRestingStatus(); return; }
      await submit(() => call('/edits', Core.editsBody(edits, state.session.revision)));    // one drag, one request
      return;
    }
    // Not a gizmo drag: a click (little movement) selects; a longer drag was an orbit.
    if (cancelled || Math.hypot(e.clientX - g.x, e.clientY - g.y) > CLICK_PIXELS) return;
    const hit = Core.pick(pointerRay(e).ldraw, shown.placements, shown.boxes);
    state.selected = Core.nextSelection(state.selected, hit ? hit.id : null, g.shift || e.shiftKey);
    drawOverlay();
    refreshControls();
    if (hit && !state.editable.has(hit.id)) {
      setStatus(`${hit.id} comes from a repeated placement in the plan and cannot be edited here.`, true);
    } else setRestingStatus();
  }
  stage.addEventListener('pointerup', e => endGesture(e, false));
  stage.addEventListener('pointercancel', e => endGesture(e, true));

  function setRestingStatus() {
    const s = state.session;
    if (!s) return;
    const picked = state.selected.length ? ` · ${state.selected.length} selected` : '';
    setStatus(`Revision ${s.revision} · ${Core.describeParts(s.parts)}${picked}`, false);
  }

  // ---- sending edits --------------------------------------------------------------

  // Run one backend call that returns a new session view, then redraw. Errors keep the current picture.
  async function submit(run) {
    if (state.busy || state.stale) return;
    state.busy = true;
    refreshControls();
    setStatus('Rebuilding…', false);
    try {
      const session = await run();
      await show(session);
    } catch (e) {
      drawOverlay();
      if (e.status === 409) {
        setStatus(`${e.message}. Reloading the current model…`, true);
        try { await show(await call('')); } catch (again) { setStatus(again.message, true); }
      } else setStatus(`${e.message} (the model is unchanged)`, true);
    } finally {
      state.busy = false;
      refreshControls();
    }
  }

  const undo = () => state.session && state.session.undo_depth > 0 &&
    submit(() => call('/undo', { base_revision: state.session.revision }));
  const redo = () => state.session && state.session.redo_depth > 0 &&
    submit(() => call('/redo', { base_revision: state.session.revision }));
  const remove = () => {
    const edits = Core.deleteEdits(state.shown.placements, state.selected.filter(id => state.editable.has(id)));
    if (!edits.length) return;
    state.selected = [];
    return submit(() => call('/edits', Core.editsBody(edits, state.session.revision)));
  };

  buttons.undo.addEventListener('click', undo);
  buttons.redo.addEventListener('click', redo);
  buttons.del.addEventListener('click', remove);
  buttons.home.addEventListener('click', () => state.shown && resetView(state.shown));
  buttons.publish.addEventListener('click', async () => {
    state.busy = true;
    refreshControls();
    setStatus('Publishing…', false);
    try {
      const result = await call('/publish', {});
      setStatus(`Published (${(result.models || []).join(', ') || 'saved'}).`, false);
    } catch (e) { setStatus(`Could not publish: ${e.message}`, true); }
    finally { state.busy = false; refreshControls(); }
  });
  toolButtons.forEach(b => b.addEventListener('click', () => {
    state.tool = b.dataset.tool;
    drawOverlay();
    refreshControls();
  }));

  // Keyboard: Ctrl+Z, Ctrl+Shift+Z (or Ctrl+Y), Delete, Escape. The stage takes focus on click.
  addEventListener('keydown', e => {
    if (!state.shown || !canEdit() || (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName))) return;
    const mod = e.ctrlKey || e.metaKey;
    const key = e.key.toLowerCase();
    if (mod && key === 'z') { e.preventDefault(); (e.shiftKey ? redo : undo)(); }
    else if (mod && key === 'y') { e.preventDefault(); redo(); }
    else if (!mod && (e.key === 'Delete' || e.key === 'Backspace') && state.selected.length) { e.preventDefault(); remove(); }
    else if (e.key === 'Escape' && state.selected.length) { state.selected = []; drawOverlay(); refreshControls(); setRestingStatus(); }
  });

  addEventListener('resize', () => state.shown && state.shown.scene.onChange());
  addEventListener('pagehide', event => { if (!event.persisted) state.shown?.scene.dispose?.(); });

  // ---- start ------------------------------------------------------------------------

  async function begin() {
    try {
      await show(await call(''));
    } catch (e) {
      if (e.status === 404 && /no editor plan/i.test(e.message)) {
        $('start').hidden = false;
        setStatus('No editor plan for this chat yet.', false);
      } else setStatus(`Could not open the editor: ${e.message}`, true);
    }
  }
  $('start-button').addEventListener('click', async () => {
    $('start-button').disabled = true;
    try {
      const session = await call('', {});
      $('start').hidden = true;
      await show(session);
    } catch (e) { setStatus(`Could not start a model: ${e.message}`, true); }
    finally { $('start-button').disabled = false; }
  });
  begin();
})();
