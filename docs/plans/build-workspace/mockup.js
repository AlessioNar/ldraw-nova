/* Build workspace mockup: static data, no framework. The 3D canvas is a real render with clickable hotspots.
   URL options: ?theme=light|dark &panel=parts|piece|ai|gen &sel=roof &draft=2 &view=1 &mode=edit|view|player &tool=move &sheet=open */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const q = new URLSearchParams(location.search);
  if (q.get("theme")) document.documentElement.dataset.theme = q.get("theme");
  const app = $("#app");

  const COLOURS = { Blue: "#1e5aa8", Red: "#c91a09", Yellow: "#f2cd37", Green: "#237841", White: "#f4f4f4", Black: "#05131d", "Dark Bluish Gray": "#6c6e68", "Light Bluish Gray": "#a0a5a9", Tan: "#e4cd9e", Orange: "#fe8a18", "Dark Red": "#720012", Brown: "#582a12" };
  const IMG = { 1: "../deterministic-first/screenshots/gen-house-8x6.png", 2: "../deterministic-first/screenshots/gen-house-full.png", 3: "../deterministic-first/screenshots/gen-house-full.png" };

  // pieces you can click on the render (polygons in the 1000 x 800 image space; g = gizmo anchor)
  const PIECES = [
    { id: "wallf", name: "Brick 1 x 6", no: "3009", col: "White", pos: [0, 2, 0], g: [450, 520], pts: "345,368 500,448 585,500 585,672 520,655 520,575 480,550 345,440" },
    { id: "walls", name: "Brick 1 x 8", no: "3008", col: "White", pos: [8, 2, 3], g: [650, 520], pts: "585,510 722,420 722,575 565,680" },
    { id: "win", name: "Window 1 x 2 x 2", no: "60592", col: "Dark Bluish Gray", pos: [8, 3, 2], g: [645, 565], pts: "615,552 672,520 672,590 618,612" },
    { id: "roof", name: "Slope 45 2 x 4", no: "3037", col: "Blue", pos: [4, 9, 3], g: [600, 390], pts: "330,345 390,213 425,195 690,322 748,403 600,500" },
    { id: "dormer", name: "Window 1 x 2 x 2 (dormer)", no: "60592", col: "White", pos: [3, 7, 1], g: [465, 380], pts: "400,332 430,310 525,352 535,380 500,440 400,385" },
    { id: "chimney", name: "Brick 2 x 2", no: "3003", col: "Dark Bluish Gray", pos: [6, 10, 2], g: [590, 250], pts: "553,205 590,188 627,205 627,290 590,310 553,290" },
    { id: "porch", name: "Plate 6 x 8", no: "3036", col: "Blue", pos: [0, 5, -4], g: [420, 490], pts: "320,462 420,422 517,498 450,560" },
    { id: "pilr", name: "Brick 2 x 2", no: "3003", col: "Dark Bluish Gray", pos: [4, 2, -4], g: [480, 600], pts: "432,555 450,562 517,500 517,640 450,692 432,660" },
    { id: "pill", name: "Brick 2 x 2", no: "3003", col: "Dark Bluish Gray", pos: [0, 2, -4], g: [340, 545], pts: "320,472 360,492 360,610 332,600" },
  ];
  // the person's own parts: own = bought, used = in the model right now (remaining = own - used)
  const MINE = [
    { no: "3004", name: "Brick 1 x 2", col: "Tan", own: 50, used: 0 },
    { no: "3010", name: "Brick 1 x 4", col: "White", own: 30, used: 14 },
    { no: "3009", name: "Brick 1 x 6", col: "White", own: 24, used: 16 },
    { no: "3008", name: "Brick 1 x 8", col: "White", own: 12, used: 6 },
    { no: "3003", name: "Brick 2 x 2", col: "Dark Bluish Gray", own: 16, used: 11 },
    { no: "3037", name: "Slope 45 2 x 4", col: "Blue", own: 18, used: 20 },
    { no: "60592", name: "Window 1 x 2 x 2", col: "White", own: 6, used: 4 },
    { no: "3036", name: "Plate 6 x 8", col: "Blue", own: 1, used: 1 },
  ];
  const LIB = [
    { no: "3002", name: "Brick 2 x 3" }, { no: "3622", name: "Brick 1 x 3" }, { no: "3005", name: "Brick 1 x 1" }, { no: "3031", name: "Plate 4 x 4" },
    { no: "3023", name: "Plate 1 x 2" }, { no: "2431", name: "Tile 1 x 4" }, { no: "3039", name: "Slope 45 2 x 2" }, { no: "3298", name: "Slope 33 3 x 2" },
  ];
  const BASE_PARTS = 112;
  const S = {
    base: 3, view: null, branched: false, changes: [], redo: [], nextV: 4, mode: "edit", tool: "select", panel: "parts", prevPanel: "parts",
    sel: null, newCol: "Blue", paint: {}, gone: {}, added: {}, parts: BASE_PARTS, dirtyGen: false, aiState: "proposed",
    versions: [
      { n: 1, kind: "Generator", parts: 64, miss: 0, when: "2 days ago" },
      { n: 2, kind: "Generator", parts: 96, miss: 6, when: "yesterday" },
      { n: 3, kind: "Edited by you", parts: 112, miss: null, when: "this morning" },
    ],
  };
  const byId = (id) => PIECES.find((p) => p.id === id);
  const mineOf = (no) => MINE.find((m) => m.no === no);
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

  // ---------- scene ----------
  const svgNS = "http://www.w3.org/2000/svg";
  const polys = $("#polys");
  const paintSvg = document.createElementNS(svgNS, "svg");
  paintSvg.setAttribute("viewBox", "0 0 1000 800"); paintSvg.id = "paint";
  paintSvg.style.cssText = "position:absolute;inset:0;width:100%;height:100%;mix-blend-mode:color;pointer-events:none";
  $("#hot").before(paintSvg);
  const defs = document.createElementNS(svgNS, "defs");
  defs.innerHTML = '<mask id="roofmask" maskUnits="userSpaceOnUse" x="0" y="0" width="1000" height="800"><rect width="1000" height="800" fill="#fff"/>' +
    ["dormer", "chimney"].map((i) => `<polygon points="${PIECES.find((p) => p.id === i).pts}" fill="#000"/>`).join("") +
    '<polygon points="625,470 655,392 690,368 740,402 722,422 650,482" fill="#000"/></mask>';
  paintSvg.append(defs);
  PIECES.forEach((p) => {
    const el = document.createElementNS(svgNS, "polygon");
    el.setAttribute("points", p.pts); el.setAttribute("class", "poly"); el.dataset.id = p.id;
    el.addEventListener("click", (e) => clickPiece(p.id, e));
    polys.append(el);
    const pp = document.createElementNS(svgNS, "polygon");
    pp.setAttribute("points", p.pts); pp.setAttribute("fill", "none"); pp.dataset.id = p.id; if (p.id === "roof") pp.setAttribute("mask", "url(#roofmask)"); paintSvg.append(pp);
  });

  function renderScene() {
    $$(".poly").forEach((el) => {
      const id = el.dataset.id;
      el.classList.toggle("sel", S.sel === id);
      el.classList.toggle("gone", !!S.gone[id]);
    });
    $$("polygon[data-id]", paintSvg).forEach((el) => { const c = S.paint[el.dataset.id]; el.setAttribute("fill", c ? COLOURS[c] : "none"); el.setAttribute("opacity", c ? ".85" : "0"); });
    const ro = S.view !== null;
    $("#render").src = IMG[ro ? S.view : 3];
    $("#polys").style.display = ro ? "none" : "";
    paintSvg.style.display = ro ? "none" : "";
    const g = $("#gizmo");
    const p = S.sel && byId(S.sel);
    g.hidden = !(p && S.mode === "edit" && !ro && ["move", "rotate"].includes(S.tool));
    if (p) g.setAttribute("transform", `translate(${p.g[0]} ${p.g[1]})`);
    $("#giz-move").style.display = S.tool === "move" ? "" : "none";
    $("#giz-rot").style.display = S.tool === "rotate" ? "" : "none";
    const chip = $("#sel-chip");
    chip.hidden = !(p && !ro);
    if (p) chip.innerHTML = `<strong>${p.name}</strong> <span class="mono">${p.no} &middot; ${p.col}${S.paint[p.id] ? " &rarr; " + S.paint[p.id] : ""}</span>`;
    const count = ro ? S.versions.find((v) => v.n === S.view).parts : S.parts;
    $("#count-chip").textContent = `${count} parts`;
    $("#snap").hidden = ro || S.mode !== "edit";
  }

  // ---------- history ----------
  function change(desc, doFn, undoFn, quiet) {
    if (S.view !== null) return;
    doFn && doFn();
    S.changes.push({ desc, doFn, undoFn }); S.redo = [];
    S.branched = true;
    if (!quiet) toast(`${desc}`, "Undo", undo);
    refresh();
  }
  function undo() {
    const c = S.changes.pop(); if (!c) return;
    c.undoFn && c.undoFn(); S.redo.push(c); toast(`Undid: ${c.desc}`, "Redo", redo); refresh();
  }
  function redo() {
    const c = S.redo.pop(); if (!c) return;
    c.doFn && c.doFn(); S.changes.push(c); refresh();
  }
  const draftCount = () => S.changes.length;
  const isDraft = () => draftCount() > 0 || (S.branched && S.base !== 3);

  // ---------- chrome ----------
  function parts() { // parts check numbers for what is on the canvas
    const v = S.view !== null ? S.versions.find((x) => x.n === S.view) : null;
    if (v && v.miss !== null) return { total: v.parts, miss: v.miss, what: v.miss ? "" : "" };
    let miss = 0; const list = [];
    MINE.forEach((m) => { const r = m.own - m.used; if (r < 0) { miss += -r; list.push(`${m.name} x${-r}`); } });
    const lib = Object.values(S.added).reduce((a, b) => a + b, 0); // library parts you do not own
    if (lib) { miss += lib; list.push(`library parts x${lib}`); }
    return { total: v ? v.parts : S.parts, miss, list };
  }
  function refresh() {
    const n = draftCount(), ro = S.view !== null;
    app.classList.toggle("ro", ro);
    app.dataset.mode = S.mode; app.dataset.tool = S.tool;
    const chip = $("#draft-chip");
    if (ro) { chip.className = "status ro"; chip.innerHTML = `Viewing v${S.view} <span class="muted">&middot; read-only</span>`; }
    else if (isDraft()) { chip.className = "status draft"; chip.innerHTML = `v${S.base} &middot; draft, ${n}<span class="w"> ${n === 1 ? "change" : "changes"}</span><span class="auto"> &middot; autosaved</span>`; }
    else { chip.className = "status"; chip.innerHTML = `v${S.base} &middot; saved<span class="auto"> &middot; editing latest</span>`; }
    $("#undo").disabled = ro || !S.changes.length; $("#redo").disabled = ro || !S.redo.length;
    $("#save").textContent = `Save as v${S.nextV}`; $("#save").disabled = ro || !isDraft();
    $("#discard").disabled = ro || !isDraft();
    $("#ro-banner").hidden = !ro;
    if (ro) {
      const v = S.versions.find((x) => x.n === S.view);
      $("#ro-v").textContent = `v${v.n}`; $("#ro-meta").textContent = `${v.kind} · ${v.parts} parts · ${v.when}`;
      $("#back-draft").textContent = isDraft() ? "Back to draft" : `Back to latest (v${S.base})`;
    }
    renderStrip(); renderPcheck(); renderScene(); renderParts(); renderPiece();
    $("#gen-warn").hidden = !(S.dirtyGen && n > 0);
    $("#gen-warn").textContent = `Regenerating replaces your ${plural(n, "manual edit")} with the new settings. Undo brings them back.`;
    $("#gen-apply").disabled = !S.dirtyGen;
    $("#mode-note").hidden = !(S.mode === "view");
    $("#mode-note").innerHTML = '<span class="chip">View mode: orbit and inspect, nothing can change</span>';
    $("#player").hidden = S.mode !== "player";
    $("#render").style.clipPath = S.mode === "player" ? `inset(${88 - (+$("#p-range").value / 38) * 64}% 0 0 0)` : "";
  }
  function renderStrip() {
    const strip = $("#vstrip"); strip.textContent = "";
    S.versions.forEach((v) => {
      const b = document.createElement("button"); b.className = "v"; b.setAttribute("role", "tab");
      const latest = v.n === S.versions[S.versions.length - 1].n;
      const sel = S.view === v.n || (S.view === null && !isDraft() && v.n === S.base);
      b.setAttribute("aria-selected", sel);
      const miss = v.n === S.versions.length || v.miss === null ? parts().miss : v.miss;
      const m = v.miss === null ? (S.view === null && !isDraft() ? parts().miss : 2) : v.miss;
      b.innerHTML = `<span class="t"><span class="mono">v${v.n}</span> ${v.kind}</span><span class="s"><span class="mono">${v.parts} parts</span><span class="ck ${m ? "warn" : "ok"}" title="${m ? "Missing " + m : "You own all of it"}"><svg><use href="#i-${m ? "warn" : "check"}"/></svg></span>${latest && !isDraft() && S.view === null ? "editing" : ""}</span>`;
      b.onclick = () => openVersion(v.n); strip.append(b);
    });
    if (isDraft()) {
      const d = document.createElement("button"); d.className = "v draft"; d.setAttribute("role", "tab");
      d.setAttribute("aria-selected", S.view === null);
      const p = parts();
      d.innerHTML = `<span class="t">Draft <span class="mono">on v${S.base}</span></span><span class="s"><span class="mono">${S.parts} parts</span><span class="ck ${p.miss ? "warn" : "ok"}"><svg><use href="#i-${p.miss ? "warn" : "check"}"/></svg></span>${plural(draftCount(), "change")}</span>`;
      d.onclick = () => { S.view = null; refresh(); }; strip.append(d);
    }
  }
  function renderPcheck() {
    const p = parts(), b = $("#pcheck"); b.className = "pcheck " + (p.miss ? "warn" : "ok");
    const own = p.total - p.miss;
    b.innerHTML = `<span class="ck ${p.miss ? "warn" : "ok"}"><svg><use href="#i-${p.miss ? "warn" : "check"}"/></svg></span><span class="txt">Parts check:</span> <span class="mono">${own} of ${p.total}</span> <span class="txt">${p.miss ? "owned, " + p.miss + " missing" : "owned"}</span>`;
    b.title = p.miss ? "Missing: " + (p.list || ["see the shopping list"]).join(", ") + ". Click to list them." : "You own every part in this model.";
    b.onclick = () => toast(p.miss ? `Missing ${p.miss}: ${(p.list || ["some parts"]).join(", ")}` : "You own every part in this model.");
  }
  function openVersion(n) {
    if (S.view === null && n === S.base && !isDraft()) return;
    if (n === S.versions[S.versions.length - 1].n && !isDraft()) { S.view = null; return refresh(); }
    S.view = n; S.sel = null; refresh();
  }

  // ---------- selection / tools ----------
  function clickPiece(id) {
    if (S.mode !== "edit" || S.view !== null) return;
    const p = byId(id);
    if (S.tool === "paint") { const old = S.paint[id] || p.col; if (old === S.newCol) return; return change(`Painted ${p.name}: ${old} to ${S.newCol}`, () => (S.paint[id] = S.newCol), () => (S.paint[id] = old === p.col ? undefined : old)); }
    if (S.tool === "delete") return doDelete(id);
    if (S.tool === "add") { return addPart({ no: "3003", name: "Brick 2 x 2" }, true); }
    select(id);
  }
  function select(id) {
    S.sel = id;
    if (id) { if (S.panel !== "piece") S.prevPanel = S.panel; setPanel("piece", true); }
    refresh();
  }
  function setTool(t) { S.tool = t; refresh(); }
  function setMode(m) { S.mode = m; if (m !== "edit") { S.sel = null; } if (m === "player") stopPlay(); refresh(); }
  function doDelete(id) {
    const p = byId(id); if (S.gone[id]) return;
    const m = mineOf(p.no);
    change(`Deleted ${p.name}`, () => { S.gone[id] = true; S.parts--; if (m) m.used--; S.sel = null; }, () => { S.gone[id] = false; S.parts++; if (m) m.used++; });
  }
  function addPart(part, fromCanvas) {
    const m = mineOf(part.no), col = S.newCol;
    change(`Added ${part.name}, ${col}`, () => { S.parts++; if (m) m.used++; else S.added[part.no] = (S.added[part.no] || 0) + 1; bump = part.no; }, () => { S.parts--; if (m) m.used--; else S.added[part.no]--; });
  }
  let bump = null;

  // ---------- panels ----------
  function setPanel(p, silent) {
    S.panel = p;
    $$("#tabs [role=tab]").forEach((t) => t.setAttribute("aria-selected", t.dataset.panel === p));
    $$(".panel").forEach((s) => (s.hidden = s.dataset.panel !== p));
    if (p === "ai") { $("#ai-dot").hidden = true; const c = $("#chat"); c.scrollTop = c.scrollHeight; }
    if (!silent && matchMedia("(max-width:760px)").matches) app.classList.add("sheet-open");
  }
  $$("#tabs [role=tab]").forEach((t) => (t.onclick = () => {
    const narrow = matchMedia("(max-width:760px)").matches;
    if (narrow && S.panel === t.dataset.panel && app.classList.contains("sheet-open")) return app.classList.remove("sheet-open");
    setPanel(t.dataset.panel);
  }));
  $("#sheet-handle").onclick = () => app.classList.toggle("sheet-open");

  function swatch(name, pressed, onclick) {
    const b = document.createElement("button"); b.className = "sw"; b.style.background = COLOURS[name]; b.title = name; b.setAttribute("aria-label", name);
    b.setAttribute("aria-pressed", pressed); b.onclick = onclick; return b;
  }
  function row(m, mineRow) {
    const li = document.createElement("li"); li.className = "part";
    const colHex = COLOURS[mineRow ? m.col : S.newCol];
    const rem = mineRow ? m.own - m.used : null;
    li.classList.toggle("low", mineRow && rem >= 0 && rem <= 3); li.classList.toggle("zero", mineRow && rem <= 0);
    if (bump === m.no) { li.classList.add("bump"); }
    const pct = mineRow ? Math.max(0, Math.min(100, (rem / m.own) * 100)) : 0;
    li.innerHTML = `<span class="pic" style="background:${colHex}"></span><span class="nm">${m.name}<small>${m.no}${mineRow ? " &middot; " + m.col : ""}</small></span>` +
      (mineRow ? `<span class="left">${rem < 0 ? "missing " + -rem : rem + " left"}<i><b style="width:${pct}%"></b></i></span>` : `<span class="tag">not owned</span>`);
    li.title = "Click to add one to the draft (Add tool)";
    li.onclick = () => { if (S.mode !== "edit" || S.view !== null) return toast("Switch to Edit mode on the latest version to add parts."); S.tool = "add"; addPart(m); };
    return li;
  }
  function renderParts() {
    const term = ($("#part-search").value || "").toLowerCase();
    const f = (m) => !term || (m.name + " " + m.no + " " + (m.col || "")).toLowerCase().includes(term);
    const mine = $("#mine"), lib = $("#library"); mine.textContent = ""; lib.textContent = "";
    MINE.filter(f).forEach((m) => mine.append(row(m, true)));
    LIB.filter(f).forEach((m) => lib.append(row(m, false)));
    if (!mine.children.length) mine.innerHTML = '<li class="part"><span></span><span class="nm muted">None of yours match</span><span></span></li>';
    bump = null;
    const sw = $("#swatches"); sw.textContent = "";
    Object.keys(COLOURS).forEach((c) => sw.append(swatch(c, c === S.newCol, () => { S.newCol = c; $("#col-name").textContent = c; renderParts(); })));
    $("#col-name").textContent = S.newCol;
  }
  function renderPiece() {
    const p = S.sel && byId(S.sel), has = !!p;
    $("#piece-empty").hidden = has; $("#piece-body").hidden = !has;
    $("#piece-dot").hidden = !(has && S.panel !== "piece");
    if (!p) return;
    const col = S.paint[p.id] || p.col, m = mineOf(p.no);
    $("#pc-name").textContent = p.name; $("#pc-no").textContent = p.no; $("#pc-col").textContent = col;
    $("#pc-swatch").style.background = COLOURS[col];
    $("#pc-owned").textContent = m ? (m.own - m.used >= 0 ? `${m.own - m.used} of ${m.own} left` : `${-(m.own - m.used)} missing`) : "not owned";
    const sw = $("#pc-swatches"); sw.textContent = "";
    Object.keys(COLOURS).forEach((c) => sw.append(swatch(c, c === col, () => { if (c === col) return; const old = S.paint[p.id]; change(`Painted ${p.name}: ${col} to ${c}`, () => (S.paint[p.id] = c), () => (S.paint[p.id] = old)); })));
    $("#px").value = p.pos[0]; $("#py").value = p.pos[1]; $("#pz").value = p.pos[2];
  }
  $("#part-search").oninput = renderParts;
  $$("[data-act]").forEach((b) => (b.onclick = () => {
    const p = byId(S.sel); if (!p) return; const a = b.dataset.act;
    if (a === "nudge-l" || a === "nudge-r") { const d = a === "nudge-l" ? -1 : 1; return change(`Moved ${p.name} ${Math.abs(d)} stud ${d < 0 ? "left" : "right"}`, () => (p.pos[0] += d), () => (p.pos[0] -= d)); }
    if (a === "up") return change(`Moved ${p.name} up 1 plate`, () => (p.pos[1] += 1), () => (p.pos[1] -= 1));
    if (a === "rot") return change(`Rotated ${p.name} 90 degrees`, () => ($("#pry").value = 90), () => ($("#pry").value = 0));
    if (a === "dup") return addPart(p);
    if (a === "del") return doDelete(p.id);
    if (a === "group") return toast("Selected the sub-model this piece belongs to (the porch: 4 pieces).");
  }));

  // ---------- AI panel ----------
  $("#ai-keep").onclick = () => {
    if (S.view !== null) return;
    $("#ai-card").className = "ai-card done"; $("#ai-card").innerHTML = '<div class="label">In your draft</div><div class="mono diffline"><span class="ok-t">+ 14 parts</span> <span class="muted">&middot; Undo takes it out again</span></div>';
    $("#ai-meta").textContent = "draft · AI · 0.06 EUR · kept";
    change("AI: garden fence (+14 parts)", () => { S.parts += 14; mineOf("3010").used += 12; mineOf("3003").used += 2; }, () => { S.parts -= 14; mineOf("3010").used -= 12; mineOf("3003").used -= 2; });
  };
  $("#ai-drop").onclick = () => { $("#ai-card").className = "ai-card done"; $("#ai-card").innerHTML = '<div class="label">Discarded</div><div class="muted small">The fence proposal was thrown away. Nothing changed.</div>'; };
  $("#ai-show").onclick = () => toast("Preview: the 14 new parts would show as a ghost on the canvas (not drawn in this mockup).");
  $("#ai-send").onclick = () => {
    const t = $("#ai-text").value.trim(); if (!t) return;
    const chat = $("#chat");
    chat.insertAdjacentHTML("beforeend", `<div class="msg user"><p></p></div><div class="msg agent"><p>Mockup only: nothing is sent. In the app this proposes a reviewable draft like the card above, or a new version if you chose that.</p><span class="meta mono">AI &middot; asks before it spends</span></div>`);
    chat.querySelectorAll(".msg.user p").forEach((p, i, a) => { if (i === a.length - 1) p.textContent = t; });
    $("#ai-text").value = ""; $(".panel[data-panel=ai]").scrollTop = 1e5;
  };

  // ---------- generator settings ----------
  const gf = $("#gen-form");
  $$("[data-k]", gf).forEach((el) => {
    const out = el.parentElement.querySelector("output"); el.dataset.def = el.type === "checkbox" ? el.checked : el.value;
    el.oninput = el.onchange = () => { if (out) out.textContent = el.value; S.dirtyGen = $$("[data-k]", gf).some((x) => String(x.type === "checkbox" ? x.checked : x.value) !== x.dataset.def); $("#gen-status").textContent = S.dirtyGen ? "Free, about 1 s. Nothing changes until you regenerate." : "Free, about 1 s"; refresh(); };
  });
  $("#gen-reset").onclick = () => { $$("[data-k]", gf).forEach((el) => { if (el.type === "checkbox") el.checked = el.dataset.def === "true"; else el.value = el.dataset.def; const o = el.parentElement.querySelector("output"); if (o) o.textContent = el.value; }); S.dirtyGen = false; refresh(); };
  $("#gen-apply").onclick = () => {
    const diffs = $$("[data-k]", gf).filter((x) => String(x.type === "checkbox" ? x.checked : x.value) !== x.dataset.def).map((x) => `${x.dataset.k} ${x.type === "checkbox" ? (x.checked ? "on" : "off") : x.value}`);
    const roof = $("#gen-roof").value;
    S.dirtyGen = false; $$("[data-k]", gf).forEach((x) => (x.dataset.def = x.type === "checkbox" ? x.checked : x.value));
    change(`Regenerated: ${diffs.join(", ")}`, () => { if (roof !== "Blue") S.paint.roof = roof; }, () => (S.paint.roof = undefined));
  };

  // ---------- describe bar ----------
  const HINT0 = "Rules first: colours, sizes and features are free and take about a second. The AI only runs if you ask.";
  function parseDescribe(t) {
    t = t.toLowerCase();
    const col = Object.keys(COLOURS).find((c) => t.includes(c.toLowerCase()));
    if (/roof/.test(t) && col) return { kind: "fast", what: `roof colour ${S.paint.roof || "Blue"} to ${col}`, run: () => { const old = S.paint.roof; if ((old || "Blue") === col) return toast(`The roof is already ${col}.`); change(`Roof colour: ${old || "Blue"} to ${col}`, () => (S.paint.roof = col), () => (S.paint.roof = old)); } };
    if (/chimney/.test(t)) return { kind: "fast", what: "chimney: " + (S.gone.chimney ? "add it back" : "remove it") + " (generator feature)", run: () => (S.gone.chimney ? change("Chimney added", () => { S.gone.chimney = false; S.parts += 3; }, () => { S.gone.chimney = true; S.parts -= 3; }) : change("Chimney removed", () => { S.gone.chimney = true; S.parts -= 3; }, () => { S.gone.chimney = false; S.parts += 3; })) };
    if (/(wider|bigger|larger|wide)/.test(t)) return { kind: "fast", what: "width 8 to 10 studs, regenerates the walls", run: () => change("Width: 8 to 10 (regenerated)", () => (S.parts += 18), () => (S.parts -= 18)) };
    return null;
  }
  function descHint() {
    const t = $("#desc-input").value.trim(), h = $("#desc-hint");
    if (t.length < 3) { h.textContent = HINT0; return; }
    const r = parseDescribe(t);
    h.innerHTML = r ? `<b>Fast path:</b> ${r.what}. Free, about 1 s. Lands in your draft, Undo works.` : `<b class="cost">No rule fits this.</b> The AI can try: about 0.05 EUR, 20-40 s. It asks before it spends, and its result arrives as a draft you review.`;
  }
  $("#desc-input").oninput = descHint;
  function describeGo() {
    const t = $("#desc-input").value.trim(); if (!t) return;
    if (S.view !== null) return toast("This is a read-only version. Branch from here or go back to the draft first.");
    const r = parseDescribe(t);
    if (r) { r.run(); $("#desc-input").value = ""; descHint(); }
    else { $("#ai-text").value = t; setPanel("ai"); toast("No rule fits, so nothing was spent. Review the estimate in the AI panel and send when you want."); }
  }
  $("#desc-go").onclick = describeGo;
  $("#desc-input").onkeydown = (e) => { if (e.key === "Enter") describeGo(); };
  descHint();

  // ---------- save / discard / versions ----------
  $("#save").onclick = save;
  function save() {
    if (!isDraft() || S.view !== null) return;
    const p = parts(), n = S.nextV;
    S.versions.push({ n, kind: "Edited by you", parts: S.parts, miss: null, when: "just now" });
    toast(`Saved as v${n}. ${plural(draftCount(), "change")} are now part of the version.`);
    S.base = n; S.nextV = n + 1; S.changes = []; S.redo = []; S.branched = false; refresh();
  }
  $("#discard").onclick = () => {
    if (!isDraft()) return;
    if (!draftCount() || confirm(`Discard the draft and its ${plural(draftCount(), "change")}?`)) {
      while (S.changes.length) { const c = S.changes.pop(); c.undoFn && c.undoFn(); }
      S.redo = []; S.branched = false; S.base = S.versions[S.versions.length - 1].n; refresh(); toast("Draft discarded. Back on the latest version.");
    }
  };
  $("#branch").onclick = () => {
    const n = S.view;
    if (draftCount() && !confirm(`Replace your draft (${plural(draftCount(), "change")}) with a new one from v${n}?`)) return;
    S.base = n; S.view = null; S.changes = []; S.redo = []; S.branched = true; S.parts = S.versions.find((v) => v.n === n).parts;
    toast(`New draft from v${n}. Edit freely, then save it as v${S.nextV}.`); refresh();
  };
  $("#back-draft").onclick = () => { S.view = null; refresh(); };
  $("#undo").onclick = undo; $("#redo").onclick = redo;

  // ---------- rail / modes / player ----------
  $$(".tool").forEach((b) => (b.onclick = () => { if (S.mode !== "edit") return; $$(".tool").forEach((x) => x.setAttribute("aria-pressed", x === b)); setTool(b.dataset.tool); if (b.dataset.tool === "add" || b.dataset.tool === "paint") setPanel("parts"); }));
  $$(".mode").forEach((b) => (b.onclick = () => { $$(".mode").forEach((x) => x.setAttribute("aria-pressed", x === b)); setMode(b.dataset.mode); }));
  let timer = null;
  function stopPlay() { clearInterval(timer); timer = null; $("#p-play use").setAttribute("href", "#i-play"); }
  $("#p-play").onclick = () => {
    if (timer) return stopPlay();
    const r = $("#p-range"); if (+r.value >= 38) r.value = 1;
    $("#p-play use").setAttribute("href", "#i-pause");
    timer = setInterval(() => { r.value = +r.value + 1; r.oninput(); if (+r.value >= 38) stopPlay(); }, 280);
  };
  $("#p-range").oninput = () => { $("#p-label").textContent = `Step ${$("#p-range").value} / 38`; refresh(); };
  $("#stage").addEventListener("click", (e) => { if (e.target.closest(".poly,button,#player,#sel-chip,#ro-banner")) return; if (S.sel) { S.sel = null; setPanel(S.prevPanel, true); refresh(); } });

  // ---------- keyboard ----------
  document.addEventListener("keydown", (e) => {
    if (/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
    const k = e.key.toLowerCase(), c = e.ctrlKey || e.metaKey;
    if (c && k === "z") { e.preventDefault(); return e.shiftKey ? redo() : undo(); }
    if (c && k === "y") { e.preventDefault(); return redo(); }
    if (c && k === "s") { e.preventDefault(); return save(); }
    const tools = { v: "select", m: "move", r: "rotate", b: "add", p: "paint" };
    if (tools[k] && S.mode === "edit") return $(`.tool[data-tool=${tools[k]}]`).click();
    if (k === "delete" || k === "backspace") { if (S.sel && S.mode === "edit") doDelete(S.sel); }
    if (k === "escape" && S.sel) { S.sel = null; setPanel(S.prevPanel, true); refresh(); }
    if (k === " " && S.mode === "player") { e.preventDefault(); $("#p-play").click(); }
  });

  // ---------- toast ----------
  let tt = null;
  function toast(msg, action, fn) {
    const t = $("#toast"); t.textContent = msg; t.hidden = false;
    if (action) { const b = document.createElement("button"); b.textContent = action; b.onclick = () => { t.hidden = true; fn(); }; t.append(b); }
    clearTimeout(tt); tt = setTimeout(() => (t.hidden = true), 4500);
  }

  // ---------- URL presets (for screenshots and demos) ----------
  const n = parseInt(q.get("draft") || "0", 10);
  const demo = [["Roof colour: Blue to Red", () => (S.paint.roof = "Red")], ["Moved Brick 2 x 2 1 stud right", null], ["Added Brick 1 x 4, White", () => { S.parts++; mineOf("3010").used++; }], ["Deleted Brick 2 x 2", null]];
  for (let i = 0; i < n; i++) { const d = demo[i % demo.length]; d[1] && d[1](); S.changes.push({ desc: d[0], doFn: d[1], undoFn: null }); S.branched = true; }
  if (q.get("mode")) { S.mode = q.get("mode"); $$(".mode").forEach((x) => x.setAttribute("aria-pressed", x.dataset.mode === S.mode)); }
  if (q.get("tool")) { S.tool = q.get("tool"); $$(".tool").forEach((x) => x.setAttribute("aria-pressed", x.dataset.tool === S.tool)); }
  if (q.get("sel")) { S.sel = q.get("sel"); }
  if (q.get("view")) S.view = parseInt(q.get("view"), 10);
  if (S.mode === "player") $("#p-range").value = q.get("step") || 21, $("#p-label").textContent = `Step ${$("#p-range").value} / 38`;
  if (q.get("sheet") === "open") app.classList.add("sheet-open");
  setPanel(q.get("panel") || (S.sel ? "piece" : "parts"), true);
  refresh();
})();
