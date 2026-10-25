# 03 3D editor view

**Goal:** a page where the person sees the model in 3D and moves parts with the mouse.

**Read first:** `app/web/viewer/viewer.js` (it already has orbit, pan and walk controls, but is read-only), `app/web/viewer/viewer.html`, `app/web/frontend/src/components/ViewerModal.tsx`, and the client in `api.ts`.

**Build:**
- An "Edit" mode in the viewer. A click selects a part by raycasting against the part's mesh, not the whole model. Shift-click adds to the selection.
- Move and rotate with a transform gizmo. The gizmo's drag result goes to task 02's `edits` endpoint on release, not on every frame.
- Undo and redo (Ctrl+Z, Ctrl+Shift+Z) call the endpoints.
- Show the rebuild status and any error next to the model. Keep the last good render on screen when a rebuild fails.
- Leave the existing read-only inspection mode unchanged. Edit is opt-in.

**Acceptance:**
- Selecting, moving and undoing a part works with the mouse alone, and the keyboard shortcuts work when the canvas has focus.
- No edit request is sent per frame. One drag sends one request.
- Inspection works at phone width. Editing needs a desktop, and the UI says so.

**Check:** once the page exists, use the Playwright MCP tools against `http://localhost:8765` and look at the screenshots.

**Out of scope:** the parts palette (task 04).
