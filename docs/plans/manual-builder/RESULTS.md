# Manual builder: end-to-end results (task 06)

Run against a throwaway container (`ldraw-nova-test-b`, port 8782, named data volume) built from this branch, driven by
Chrome (software rendering, `--use-angle=swiftshader`) through Playwright:

    cd app/web/viewer-deps && npm ci
    VIEWER_TEST_URL=http://127.0.0.1:8782 PLAYWRIGHT_CHANNEL=chrome npm run test:editor

Result: 5 of 5 tests pass (75.7 s). Pure-logic tests (`npm run test:core`): 14 of 14 pass.

The first full run found real problems; they are fixed in this branch and listed at the end.

## Checks

| Test | Result | Numbers |
|---|---|---|
| Open, select, move, undo, redo, publish | pass | see below |
| Delete a selected part | pass | 2 parts to 1 |
| Round trip: hand-edit plan on disk, rebuild, reload | pass | MPD identical to a toolkit rebuild of the saved plan (269 bytes), on disk and as served by the API |
| Fault: kill the server during an edit | pass | 3 kills, plan and preview intact each time (below) |
| Load: 2,000 parts | pass | below |

### Open, select, move, undo, redo, publish (screenshots: `01-open.png`, `02-selected.png`, `03-moved.png`)

- First-load framing of a 1x1 brick: one 20 LDU stud spans 103 px (it was about 270 px). The model's bounding box covers 0.15 of the viewport width and 0.29 of its height. The "fit" button gives the identical camera distance.
- One wheel notch moves the camera 14.3% closer (was 5%, from OrbitControls zoomSpeed 1 (not measured before the change)); `zoomSpeed` is now 3.
- Status while dragging: `Move X +40 LDU (snapping to 20 LDU, hold Alt for free)`; with Alt held: `Move X +40.3631 LDU (free, Alt held)`.
- The drag sent exactly one `POST .../editor/edits` and the plan moved from x=0 to x=40. That request took 1.47 s as the page saw it (rebuild included). Undo restored x=0, redo x=40, Publish answered `Published (86d6f7f2e44e)`.
- `05-deleted.png`: a two-brick model after selecting one part and pressing Delete.

### Round trip

`plan.json` was edited on disk inside the container (colour and position of the base brick changed, one brick added by hand). The server notices that the plan no longer matches the state it committed: the first edit request with the old revision gets a 409 naming the new revision, and a retry with it rebuilds from the hand-edited plan. After a page reload, the hand edits are in the plan, and the MPD equals `ldraw-agent build` of the saved `plan.json` (the build tool writes CRLF line endings and `preview.mpd` on disk does too; the API returns LF, so the test compares with line endings normalised).

### Fault

An edit request was sent and the container was killed (`docker kill`) 50 ms, 400 ms and 1200 ms later, then restarted (`docker start`, same data volume).

| Kill after | Request | Revision | After restart |
|---|---|---|---|
| 50 ms | failed (connection lost) | 1 to 1 | old plan intact |
| 400 ms | failed (connection lost) | 2 to 2 | old plan intact |
| 1200 ms | answered | 3 to 4 | edit committed whole |

After each restart `plan.json` and `state.json` parsed, the revision was the old one or old+1 (never anything else), the served MPD equalled a rebuild of the plan, and the session accepted a further edit. Caveat: the three kill times landed before or after the commit, not inside the three file writes, which take milliseconds; the plan, MPD and state are written with atomic renames in the order plan, MPD, state, and the self-healing in `_load` covers a crash between them (task 01 tests), but this run did not hit that window.

### Load, 2,000 bricks (20 x 10 per layer, 10 layers, `06-big.png`)

- Opening the plan (`POST /api/chats/{id}/editor` with `{"source": "big.json"}`, which builds it): 3.66 s.
- Page load to the first `Revision 1 · 2000 parts used` status: 1.1 s (software-rendered Chrome).
- Moving one part by 20 LDU: server rebuild 4.74 s as the page saw the request, 6.57 s from mouse release to the redrawn model.
- Requests during the drag (pointer down, 8 moves, before release): 0. Exactly one edit request after release. There is no request per frame.

## Problems the checks found

1. **Editor API failed for models of more than a few hundred parts.** The backend parsed the editor tool's JSON report from stdout, which the sandbox caps at 20,000 characters, so opening the 2,000-part plan returned 500 "The editor tool did not report JSON". `app/web/backend/routers/editor.py` now has the tool write the report to a file (`--report`) and reads that. Not covered by the backend pytest suite (pytest is not in the app image; not run).
2. First-load framing was too close and wheel zoom too weak (fixed in `editor.js` and `editor-core.js`, with a unit test of `fitDistance`).
3. Hand-editing `plan.json` on disk is reported to the editor as a conflict on the next edit (by design), documented above.

Not verified: a real mouse wheel on a physical device (the test sends wheel events), touch input, and the React wrapper page (the checks use the plain `viewer/editor.html`).

# Real-model pass: the editor on an agent-built model (2026-10-23)

Run against an isolated instance (port 8785) with the benchmark starfighter `azure-dart` (51 parts, 7 sections: a root that only places six sub-models) and a purpose-made model with a sub-model placed twice (one copy turned half a circle) and a repeated run. Chrome, software rendering, Playwright.

## What a user hit, and what changed

| # | What the user saw | Cause | Fix |
|---|---|---|---|
| 1 | "Edit in 3D" on a chat with an agent-built plan: "This chat has no model to edit yet" and only "Start a new model" (a one-brick skeleton). The agent's model was unreachable from the UI (screenshot `before-no-plan-offered.png`). | Opening an existing plan needed `POST /editor` with a `source`; nothing in the page offered it. | New `GET /api/chats/{id}/editor/plans` lists the plan files in the chat's folder, newest first; the start panel shows them and opens the one clicked (`after-plan-choice.png`). A new "Open plan..." button swaps in another plan; the current session is moved to `editor/archive/<time>/`, not deleted (`replace` flag on the open call). |
| 2 | Only whole sub-models could be selected: a click on the starfighter picked "hull" (everything), never a keel plate. | Only the root section's placements were parsed and picked. | `EditorCore.flatten` walks the MPD into sub-models; every part is pickable (key `hull/a-plate`). Edits name the plan section that holds the part and convert the drag into the sub-model's own frame, so a turned wing moves the right way (`after-part-inside-submodel.png`). Moving a part of a sub-model drawn N times changes all N copies; the status line says so. "Group" (key G) selects the sub-model instance so a drag moves the whole group. |
| 3 | Repeated placements: "cannot be edited here". | Their ids (`row-1`) are not plan entries. | They map back to their entry: move acts on the whole run through its template, delete removes the run, rotate is refused with a reason ("repeated placement ... cannot be rotated"). A part in a tilted sub-model likewise gets a message instead of a silent no-op. |
| 4 | The orange selection box floated at the wrong height and the upper half of a brick was not clickable. | ldbi keeps part geometry flipped (y and z negated); the editor used it as if it were LDraw's. | Part boxes are flipped back (pre-existing bug, found with the stacked-parts test). |
| 5 | A canopy over a wing: the wing could not be picked. | The nearest box always wins. | A repeated click on the same spot steps through everything under the pointer ("2 of 3 under the pointer"); a click on the gizmo that did not drag counts as a click on the part behind it. |
| 6 | Delete on the only part of a sub-model failed with "section would have no steps left". | The plan format needs one entry per section. | The sub-model instance is deleted instead; undo restores it. |
| 7 | A brick added from the palette next to a sub-model part went into the root, snapped to the sub-model instance, and ended under the hull. | Add used the root section and the toolkit's first-ranked connection (brick underneath is ranked before brick on top). | Add goes into the selected part's own section and snaps to that part; the toolkit snap takes a new `nearest` option (editor only) that chooses the connection closest to the drop point. |
| 8 | The model filled a third of the view, tiny for a 50-part ship. | Fixed framing for a one-brick model. | Framing grows with the model, up to 60% (1-brick framing unchanged). |
| 9 | No keyboard control. | - | Arrows move a stud, Page Up/Down a plate, Shift+Left/Right a quarter turn (see below). |

Checked in the browser on the real model: open from the chat link, select parts inside sub-models, move, add from the palette, undo, redo, delete, publish; the published model appears as a card in the chat and in My Models (52 parts for the starfighter plus one added brick).

Tests: `npm run test:core` 23 pass; `editor.browser.mjs` (open, delete, round trip, load, agent-built plan, overlapping parts) and `editor-palette.browser.mjs` pass; backend `test_editor_api.py` gained three tests; `tests/test_editor.py` one. The fault test (kill the server mid-edit) fails the same way on an unmodified build in this environment (`fetch failed` after `docker start`), so it is not affected by these changes.

Not done: a ghost preview while placing a part, a snap on/off toggle (Alt for free movement exists for drags), camera keys, snapping to parts inside sub-models from the palette when the selection is a whole group.

## What other editors do (read 2026-10-23)

Sources: LeoCAD tutorial 1 (https://www.leocad.org/docs/tutorial1.html), LeoCAD README on GitHub (leozide/leocad), the Bricksmith home page (https://bricksmith.sourceforge.io/) and its GitHub mirror (bsupnik/bricksmith), and a search for web LDraw editors. The Bricksmith pages only say "drag-and-drop building" and "support for steps and submodels"; they do not describe snapping, nudging or submodel selection, so nothing is claimed about them beyond that. The web search found viewers and loaders (LasseD/buildinginstructions.js, brigl, three.js LDrawLoader) and one beta editor (Gr8brik) with no documented stud snapping; its repository page could not be fetched.

LeoCAD, as its tutorial describes it:
- Parts are dragged from the parts list into the view with a live preview of where the part will land; Insert also adds one.
- Movement snap is shown in the status bar and set per axis (Snap XY: "1 Stud"); a magnet button switches it off.
- Arrow keys, Page Up and Page Down move the selection; Shift with them rotates.
- Ctrl-click adds to the selection; Alt-drag orbits, pans and zooms with the three mouse buttons.

Borrowed, in order of value for this task:
1. Keyboard nudging (arrows = one stud, Page Up/Down = one plate, Shift+arrows = quarter turn), each press one undo step.
2. The status bar naming the active step and what is selected: ours now says which sub-model a part sits in, how many copies an edit changes, and how many parts are under the pointer.
3. A free-movement escape from the grid (their magnet toggle): we already had Alt while dragging; unchanged.
4. Preview while placing: not built; the palette still adds then snaps. It is the next thing worth doing.
