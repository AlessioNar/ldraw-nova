# Build workspace: the build page as an always-editable 3D canvas

Clickable mockup, docs only. Open `mockup.html` in a browser (no build step). Options: `?theme=light|dark`, `panel=parts|piece|ai|gen`, `sel=roof|chimney|dormer|porch|...`, `tool=move`, `draft=2` (pretend edits), `view=1` (old version), `mode=view|player`, `sheet=open` (phone). `phone.html` shows the narrow layout in two phone frames.

The canvas is a real render (`../deterministic-first/screenshots/gen-house-*.png`) with clickable hotspots, so selection, paint, delete and the draft counter are real behaviour but the picture only approximates them (paint tints the hotspot, delete blanks it). Tokens, buttons and fields come straight from `app/web/frontend/src/styles/theme.css`, fonts from `public/fonts`. As in the real viewer, the 3D panel stays light in the dark theme.

Screenshots in `screenshots/`: `01-parts`, `02-piece`, `03-ai`, `04-settings` (the four panels), `05-old-version` (viewing v1), `06-draft` (3 changes), `07-light`, `08-phone`, `09-player`, `10-light-ai`.

## Layout and why
```
 ← Builds  House [Generator: House]            v3 · draft, 2 changes   Undo Redo  Discard  [Save as v4]
 rail | 3D canvas                                              | Parts | Piece | AI | Settings
 V M R|  (selection chip, snap chip, parts count)              |  active panel
 B P  |                                                       |
 Del  |  Edit / View / Player (mode switch, bottom of rail)   |
 ---------------------------------------------------------------------------------------------------
 Describe: [make the roof blue        ]  Fast path: roof colour Blue to Red. Free, about 1 s.   [Apply]
 Versions: [v1 Generator 64 ok][v2 Generator 96 !][v3 Edited by you 112 !][Draft on v3, 2 changes]   Parts check: 110 of 112
```
- No app sidebar, only "← Builds". The canvas gets everything else.
- **Right panels are tabs, not a stack.** The four panels are each tall (palette list, form, chat). Stacked, every one would be cramped and the canvas would still be narrow; tabs keep one 344 px column and give each panel full height. The cost is hiding context, so we soften it: selecting a piece jumps to Piece (Esc goes back to the previous tab), a dot marks the Piece tab when something is selected and the AI tab when the AI has proposed something, and Settings only appears for generator builds (for imported builds it is replaced by nothing, three tabs).
- **Bottom is global, not per panel**: Describe and versions apply to the whole build whatever tab is open, so they sit under everything. Describe is a single line with a live hint (rules only, no AI call while typing).
- **Left rail** is tools (one-key shortcuts shown on the buttons) plus the Edit / View / Player switch. View and Player are the same canvas: View turns tools off and shows nothing editable, Player adds a step slider and reveals the model bottom-up (the mock fakes it with a clip).
- **Narrow windows (<= 760 px)**: rail becomes a bottom toolbar, Describe and versions stack under it, and the right panels become a **bottom sheet**: collapsed it is just the tab row (canvas keeps the screen), tapping a tab or the handle slides it to about 58% height. Selecting a piece sets the Piece tab but does not open the sheet; the selection chip on the canvas tells you. Between 760 and 1100 px the right column shrinks to 300 px and the Describe hint is hidden. Editing needs a mouse for now, as today (the old `desktop-note`), so the phone is mainly View/Player plus Describe.

## Interaction model
- **Selection**: click selects, Shift-click adds, drag on empty space box-selects, `G` widens to the sub-model, Esc clears. A dashed outline and the chip "Slope 45 2 x 4 / 3037 / Blue" show what is selected. Hover outlines in blue.
- **Tools**: Select (V), Move (M, arrow gizmo, snaps to studs/plates, Alt = free), Rotate (R, 15 degree steps), Add brick (B, uses the part and colour chosen in Parts; clicking a palette row also adds), Paint (P, click a piece to apply the palette colour), Delete (Del). Everything the old `editor.js` does stays; Add and Paint are the new tools.
- **Keyboard proposal**: `V M R B P` tools, `Del` delete, `Ctrl+Z` / `Ctrl+Shift+Z` (or `Y`) undo/redo, `Ctrl+S` save as vN, `Esc` deselect, `Home` reset view, `Space` play/pause in Player, `D` focus the Describe bar (proposal), `1`-`4` switch right tab (proposal, not in the mock). Arrow keys nudge the selection one stud.
- **Parts panel**: "Your parts" first, each row shows what is left (`remaining = bought - used in the model`); it ticks down live when you add (and up when you delete) and goes red as "missing N" when you use more than you own. Then the full library with a "not owned" tag; you can still use those parts, the parts check counts them as missing. Search filters both lists, the colour row sets the colour for new bricks and for the Paint tool.
- **Piece panel**: part, number, colour (swatches), position and rotation fields, quick actions (nudge 1 stud, up 1 plate, rotate 90, duplicate, group, delete) and "N left of M" ownership. Every action is one draft step.

## Draft and version model
- Opening a build opens its **latest version** in Edit mode. Status chip: `v3 · saved` when clean.
- The first edit creates the **draft**: `v3 · draft, 2 changes · autosaved`. Edits autosave continuously (debounced) into the draft, with undo/redo over the draft's change list (the undo stack survives a reload, since the draft stores its op list). One draft per build.
- **Save as v4** turns the draft into a version (kind "Edited by you"), the chip goes back to `v4 · saved`. **Discard draft** goes back to the latest version. Leaving the page keeps the draft; coming back shows `v3 · draft, N changes` again.
- **Version strip**: v1 Generator, v2 Generator, v3 Edited by you, then a dashed "Draft on v3" chip. Each shows its parts count and a parts-check mark. Clicking an old version switches the canvas to **"Viewing v1 (read-only)"** with **Branch from here** (new draft based on v1; if a draft exists it asks before replacing it) and **Back to draft**. Tools are disabled while viewing.
- **Parts check** (bottom right, also per version): "Parts check: 110 of 112 owned, 2 missing", green when you own everything; click lists the missing parts. It recomputes from the canvas (draft included).

## How AI, Describe and generator changes land
All three end up in the **draft**, one undo step each, so nothing bypasses the review model.
- **Describe bar** (deterministic first): the hint line runs the rules while you type ("Fast path: roof colour Blue to Red. Free, about 1 s."). Enter applies it as a draft change with a toast and Undo. If no rule fits it says so with the estimate ("No rule fits this. The AI can try: about 0.05 EUR, 20-40 s. It asks before it spends") and Enter only moves the text to the AI panel; nothing is spent until the person sends it there.
- **AI chat** (the chat already attached to this build): the AI's result is a **reviewable draft proposal** ("+14 parts, 0 removed, 0 moved", Keep in draft / Preview / Discard). Keep merges it into the draft as one change. Option for large rewrites: arrive as a new version instead (open question 3).
- **Generator settings** (generator builds only): the same form as `GeneratorForm.tsx`. Changing fields does nothing until **Regenerate** (free, about 1 s), which writes a new generated base into the draft. If the draft has manual edits a warning says "Regenerating replaces your N manual edits. Undo brings them back" (open question 1).

## Empty build state
A build with no parts (new "start blank" build, or an AI build still running): the canvas shows the grid, the four brick-colour row and one sentence ("Nothing here yet. Describe what you want below, or pick a part and click to place it."). The Describe bar is focused, the Parts tab is open, the version strip shows only "Draft on v0 · 0 parts". Same pattern as the empty 3D stage in the chat page.

## Performance notes (big models)
Must stay fast on 2,000+ part models (the Bradbury Building class):
- Open: load the latest version's geometry once; do not rebuild the scene on every edit. An edit mutates one part's transform (or adds/removes a mesh), never reloads the file.
- Picking: GPU id-buffer or BVH raycast, not a per-mesh scan on mousemove; hover highlight is throttled to a frame.
- Drag: move/rotate preview is local and instant; send the op to the draft on pointer-up only (as `editor.js` does now), batch autosave (debounce ~800 ms).
- Selection outline: outline pass on the selected set only; fall back to a flat tint above ~500 selected parts.
- Palette: virtualise the library list (1,284 parts), search is local and debounced; thumbnails lazy.
- Parts check: incremental (a count map updated per op), not a recount of the file.
- Undo/redo: store ops (part id + before/after), not scene snapshots.
- Player: precompute step order once; slider scrubs by toggling mesh visibility, not rebuilding.
- Regenerate/AI runs off the UI thread and shows progress on the draft chip; the canvas stays interactive on the old draft until the result lands.

## Implementation plan (3 agent tasks)
1. **Backend: draft and version API** (`app/web/backend`, builds and chats routes, tests). Draft per build (ops list + materialised plan), endpoints to read/patch/append ops, undo/redo, discard, `save as vN` (creates a version, kind "edited"), branch from version N, parts-check endpoint (owned vs needed, per version and for the draft), regenerate-into-draft for generator builds, AI result delivered as a draft proposal (`proposal` record) with accept/discard. Migration for existing builds (no draft). Independent of the UI; can be done and tested alone.
2. **Editor engine** (`app/web/viewer`: `editor-core.js`, `editor.js`, `glb-view.js`, `player.js`). Merge the viewer, player and editor into one embeddable canvas with modes Edit / View / Player, new Add and Paint tools, box-select, hover, selection events out (`postMessage`: selected piece, op list) and commands in (set colour, nudge, delete, undo, load version, read-only flag, step), op-based undo, themed backdrop, performance items above. Contract with task 1: ops are the unit of change.
3. **Frontend workspace** (`app/web/frontend/src`: `BuildPage.tsx`, `ViewerStage.tsx`, `DescribeBox.tsx`, `GeneratorForm.tsx`, new `components/workspace/*`, `styles/build.css`, `responsive.css`). Layout above, the four panels, tool rail, version strip with read-only view and Branch/Back, status chip and Save/Discard, Describe bar with the live hint, phone bottom sheet. Reuses the existing components (DescribeBox hint logic, GeneratorForm) and the mockup CSS. Can start against stubs of tasks 1 and 2; integrate last.

## Open questions for the owner
1. When generator settings change and the draft has manual edits, should Regenerate replace them (mockup: yes, with a warning and Undo) or try to reapply them to the new model by part position (hard, often impossible when the size changes)?
2. One draft per build (mockup) or one per version? One is simpler and matches "leaving keeps the draft"; branching from an old version then has to replace it with a confirmation.
3. Should large AI changes always arrive as a reviewable draft, or may the person choose "as a new version" (keeps history, but skips the review step)?
4. On phones: View/Player only, or full editing with touch gizmos? The mockup assumes view-only editing today (matches the current "needs a mouse" note).
