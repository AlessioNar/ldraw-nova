# Build workspace: shared contract

Three agents build the always-editable build page in parallel against this contract
(see README.md and mockup.html in this folder for the design). Owner decisions:
one draft per build; edits autosave into the draft; "Save as vN" makes a version;
leaving keeps the draft; Regenerate replaces manual edits (warning + Undo); AI changes
arrive as a reviewable proposal (Keep / Preview / Discard); phones get View + Player only.

## The draft is the build's editor session

A build's draft IS its editor session in space `builds` (`/api/builds/{slug}/editor...`,
store key `b-<slug>`, files under `data/builds/<slug>/output/editor/`). Edits, undo and redo
keep using the existing routes and ops (`add, move, rotate, recolour, delete, snap`).
The backend adds draft metadata next to the session: `draft.json`
`{base_version, base_file, base_revision, saved_revision, updated_at}`.
"changes" = current `revision` - `base_revision` (0 = clean).

## Backend endpoints (agent W1: workspace-api)

| Route | Does |
|---|---|
| `GET /api/builds/{id}/workspace` | `{build, versions: [{version, file, origin, parts, changes, parts_check: {owned, needed, missing}}], draft: {base_version, changes, revision, undo_depth, redo_depth, updated_at} \| null, generator: {id, params} \| null, proposals: [...], chat_id}` |
| `POST /api/builds/{id}/draft/open {version?, force?}` | Resume the draft, or start one from the latest version (or `version`). Starting from another version while the draft has changes → 409 `{changes}` unless `force`. Returns the editor view + `draft`. Empty build → empty plan. |
| `POST /api/builds/{id}/draft/save` | Draft → next version (wraps the existing publish: validate, `<slug>-vN.mpd`, png/csv, origin `editor`). Draft stays open on the new version with 0 changes. Returns `{version, file, message}`. |
| `POST /api/builds/{id}/draft/discard` | Back to `base_version`, 0 changes. |
| `POST /api/builds/{id}/draft/regenerate {generator, params}` | Generator output replaces the draft plan as ONE undoable step; `{replaced_manual_edits: bool}`; stores params as the build's generator source. |
| `POST /api/builds/{id}/draft/describe {text}` | Calls the fast path (`/api/describe` logic) with `current` = the build's generator source. `generator`/`edit` → regenerate into the draft (one undoable step). `needs_ai` / `clarify` → returned as-is, nothing spent, draft untouched. Returns `{route, ..., draft}`. |
| `GET /api/builds/{id}/draft/parts-check` | Draft vs the person's whole collection (all parts lists): `{owned, needed, missing: [{part, colour, need, have}]}`. |
| `GET /api/builds/{id}/proposals`, `POST .../proposals/{pid}/accept`, `POST .../proposals/{pid}/discard`, `GET .../proposals/{pid}.mpd` | When a chat attached to the build publishes a model, it becomes a proposal `{id, created_at, summary: {added, removed, moved}, cost_eur, chat_id}` instead of a version. Accept applies it to the draft as ONE undoable step. |

Edits through `/api/builds/{id}/editor/edits|undo|redo` update `draft.json.updated_at`.

## Canvas (agent W2: workspace-canvas)

One embeddable page for viewing, playing and editing:
`/viewer/editor.html?space=builds&id={slug}&embed=1&mode=edit|view|player&theme=dark|light`
(chat editor URLs keep working). `embed=1` hides the page's own toolbar/back links/panels;
the React workspace provides those. The canvas calls the editor API itself for edits.

Messages, all `window.postMessage` objects with `type` prefixed `obd:`:

Out (canvas → parent):
- `{type: "obd:ready", mode}`
- `{type: "obd:selection", pieces: [{id, part, name, colour, position: [x,y,z], rotation: [...]}]}` (empty list = nothing selected)
- `{type: "obd:changed", revision, undo_depth, redo_depth}` after every successful edit/undo/redo
- `{type: "obd:error", message}`
- `{type: "obd:mode", mode}`
- `{type: "obd:tool", tool}` whenever the active tool changes inside the canvas (shortcut or click), so the rail stays in sync
- `{type: "obd:steps", total, current}` in Player mode on load and on every step change
- `{type: "obd:shortcut", action: "save"}` when Ctrl+S / Cmd+S is pressed inside the canvas (default prevented there)

In (parent → canvas): `{type: "obd:command", cmd, ...args}` with `cmd`:
`undo`, `redo`, `delete`, `set-tool {tool: select|move|rotate|add|paint|delete}`,
`set-colour {colour}` (paint tool colour and recolour of the selection),
`add-part {part, colour}` (arm the add tool with this part), `set-mode {mode}`,
`reload` (re-fetch the editor view after a backend change: regenerate, accept, discard, open),
`view-version {url}` (read-only display of an MPD URL) and `back-to-draft`, `focus` (frame the model),
`step {n}` (player).

## Workspace UI (agent W3: workspace-ui)

`/build/:id` becomes the workspace (layout per mockup.html): top bar, tool rail, the canvas
iframe, right tabs Parts / Piece / AI / Settings, Describe bar, version strip, parts check.
It talks only to the W1 endpoints and the W2 messages above; until those land it uses stubs
that follow this contract exactly.
