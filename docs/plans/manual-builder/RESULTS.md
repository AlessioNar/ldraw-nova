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
