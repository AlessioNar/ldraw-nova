// The editor's scene: one three.js group per part, so an edit touches only the parts it changes.
//
// ldbi (the viewer's model loader) bakes every placement into its own copy of the geometry, so changing one part
// means building the whole model again. Here the part types still come from ldbi's loader (parsed once, their
// geometry built once and shared), but each placement is a Group holding meshes that share that geometry and carry
// the placement as their matrix. Move and rotate set a matrix, delete removes a group, recolour and add build one
// group. Materials are shared per colour.
//
// Plain script (needs THREE and LDR), loaded before editor.js. The decisions about WHAT changed
// (EditorCore.diffLeaves) are pure and tested under node; this file only applies them.
var EditorScene = (function () {
  const Core = EditorCore;

  // ldbi's rule for the colour of a part's own geometry: 16 is the colour it was placed in, 24 its edge colour.
  function geometryColour(tc, c) {
    if (tc === '16') return c;
    if (tc === '24') return c === 16 ? 24 : (c < 0 ? c : -c - 1);
    return parseInt(tc, 10);
  }

  // The loader's part type for a reference, following a replacement (a part that was renamed), or null while unknown.
  function partType(loader, ref) {
    const pt = loader.getPartType(String(ref).toLowerCase());
    if (pt && pt.replacement) return loader.getPartType(pt.replacement) || pt;
    return pt || null;
  }

  // A leaf a model may not draw with shared geometry (decals, a part that is really a sub-model): ldbi builds it
  // at the origin into a collector that hands the meshes over.
  function collector(group, glow) {
    const note = (c, mesh) => {
      group.add(mesh);
      if (mesh.isMesh && LDR.Colors.isTrans(c) && LDR.Colors.getLuminance(c) > 0) glow.push({ mesh, color: c });
    };
    return {
      addMesh: (c, mesh) => note(c, mesh),
      addLines: (c, mesh) => group.add(mesh),
      expandBoundingBox() {}, expandBoundingBoxByPoint() {}, addHoverBox() {},
    };
  }

  function create(scene) {
    const loader = scene.loader;
    const root = new THREE.Group();                     // inside baseObject, so the model's recentring applies
    scene.baseObject.add(root);
    const known = new Map();                            // key -> { ref, colour, tf, group, glow }
    const triangleMaterials = new Map(), lineMaterials = new Map();
    const zero = new THREE.Vector3(), identity = new THREE.Matrix3();

    const colourOk = c => (c < 0 || LDR.Colors[c] !== undefined ? c : 0);
    const triangleMaterial = c => {
      if (!triangleMaterials.has(c)) triangleMaterials.set(c, LDR.Colors.buildTriangleMaterial(c, false));
      return triangleMaterials.get(c);
    };
    const lineMaterial = (c, conditional) => {
      const key = conditional ? `${c}c` : String(c);
      if (!lineMaterials.has(key)) lineMaterials.set(key, LDR.Colors.buildLineMaterial(c, conditional));
      return lineMaterials.get(key);
    };

    function build(ref, colour) {
      const group = new THREE.Group();
      const glow = [];
      const pt = partType(loader, ref);
      if (!pt) return null;
      const c = colourOk(colour);
      let shared = false;
      if (pt.isPart) {
        pt.ensureGeometry(loader);
        pt.geometry.buildGeometries();
        shared = !Object.keys(pt.geometry.texmapGeometries || {}).length;
      }
      if (shared) {
        const g = pt.geometry;
        for (const tc of Object.keys(g.triangleGeometries)) {
          const c3 = geometryColour(tc, c);
          const mesh = new THREE.Mesh(g.triangleGeometries[tc], triangleMaterial(c3));
          group.add(mesh);
          if (LDR.Colors.isTrans(c3) && LDR.Colors.getLuminance(c3) > 0) glow.push({ mesh, color: c3 });
        }
        for (const tc of Object.keys(g.lineGeometries)) {
          group.add(new THREE.LineSegments(g.lineGeometries[tc], lineMaterial(geometryColour(tc, c), false)));
        }
        for (const tc of Object.keys(g.conditionalLineGeometries)) {
          group.add(new THREE.LineSegments(g.conditionalLineGeometries[tc], lineMaterial(geometryColour(tc, c), true)));
        }
      } else {
        pt.generateThreePart(loader, c, zero, identity, true, false, collector(group, glow), null);
      }
      group.matrixAutoUpdate = false;
      group.userData.glow = glow;
      return group;
    }

    function place(group, node) {
      group.matrix.set(...Core.sceneMatrix(node));
      group.matrixWorldNeedsUpdate = true;
    }

    function discard(key) {
      const entry = known.get(key);
      if (!entry) return false;
      root.remove(entry.group);
      known.delete(key);
      return entry.group.userData.glow.length > 0;
    }

    return {
      root, known,

      // References of the leaves that are not loaded yet.
      missing(leaves) {
        return Core.leafRefs(leaves).filter(ref => !partType(loader, ref));
      },

      // Make the scene show `nodes`' leaves. Returns the diff and whether a glowing (emissive) part came or went, in
      // which case the caller rebuilds the post-processing passes. Parts that are not loaded are skipped (see missing()).
      sync(nodes) {
        const leaves = nodes.filter(n => n.leaf);
        const eff = Core.effectiveColours(nodes);
        const diff = Core.diffLeaves(known, leaves, eff);
        const byKey = new Map(leaves.map(n => [n.key, n]));
        let glowChanged = false;
        for (const key of diff.remove) glowChanged = discard(key) || glowChanged;
        for (const key of diff.rebuild) glowChanged = discard(key) || glowChanged;
        for (const key of [...diff.add, ...diff.rebuild]) {
          const node = byKey.get(key);
          const group = build(node.ref, eff.get(key));
          if (!group) continue;
          place(group, node);
          group.userData.key = key;
          root.add(group);
          known.set(key, { ref: String(node.ref).toLowerCase(), colour: eff.get(key), tf: Core.sceneMatrix(node).join(','), group });
          glowChanged = group.userData.glow.length > 0 || glowChanged;
        }
        for (const key of diff.move) {
          const node = byKey.get(key), entry = known.get(key);
          place(entry.group, node);
          entry.tf = Core.sceneMatrix(node).join(',');
        }
        // The glow bookkeeping lives in ldbi's collector (scene.mc), which the composer reads.
        if (glowChanged && scene.mc) {
          scene.mc.glowObjects = [];
          for (const entry of known.values()) scene.mc.glowObjects.push(...entry.group.userData.glow);
        }
        return { diff, glowChanged };
      },

      // The model's box in scene coordinates before the recentring.
      bounds() {
        root.updateMatrixWorld(true);
        const box = new THREE.Box3();
        for (const entry of known.values()) box.union(new THREE.Box3().setFromObject(entry.group));
        return box;
      },

      dispose() {
        for (const m of triangleMaterials.values()) m.dispose();
        for (const m of lineMaterials.values()) m.dispose();
        triangleMaterials.clear();
        lineMaterials.clear();
        known.clear();
      },
    };
  }

  return { create, partType, geometryColour };
})();
