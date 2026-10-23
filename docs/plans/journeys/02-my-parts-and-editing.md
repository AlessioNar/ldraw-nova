# Journeys 3 and 4: build from my own parts, and agent plus manual editing

Audit only. No product code was changed. Date of the run: 2026-10-23.

## How this was run

- Isolated image `ldraw-nova-audit-2` built from this worktree, container on `127.0.0.1:8802`, own data and config volumes. The running `ldraw-nova-app` and port 8765 were not touched.
- Data copied in read-only from the owner's `app/data`: the three recorded chats (`chats/`, `output/`), the models in `generated/`, and the real inventory `pileometer-to-rebrickable-parts-1.csv` (388 rows, 798 pieces after import). The 51-part starfighter plan from the bench work folder (`bench-out/work/azure-dart.plan.json`, 7 sections, 57 entries) was copied into the starship chat's work folder under `bench/`, so the "open a plan" list has two plans. The originals were not modified.
- Playwright 1.63 with system Chrome, SwiftShader, 1440 px and 390 px. Every screenshot was read before writing this.
- No API keys, no model calls. The container has no model configured, so the composer says "No model configured yet" and Send is disabled. Where a journey needs the agent I used the owner's recorded chats (their messages, parts-check cards and model cards are real agent output). Nothing was mocked with `page.route()`. What this means: "start a build in parts mode" was driven only as far as the Send button (settings popover, mode, inventory pick); the result side (parts check card) was read from the recorded Only-mode chats.
- The real timings below come from a software-rendered, CPU-limited container; native machines will be faster, but the shape (long first scan, per-query rescans) is the same.
- Test pollution to know about: I deleted and re-imported the inventory through the UI (that is part of the journey), which first broke the recorded chats' link to it; I then re-imported it under its original name so ids matched again. That breakage is itself reported as friction F3-9.

---

# Journey 3: build from my own parts

## Steps as the user experiences them

1. My Parts, empty. Heading, one sentence about "a Rebrickable CSV", a red "+ Upload parts list" button, and an empty-state card with the same text again.
2. Upload dialog: drop zone ("Rebrickable format: Part, Color, Quantity. Up to 25 MB."), Name (pre-filled from the file name), "Count spare parts" checkbox. A non-Rebrickable file (a BrickLink-style `Item No,Color,Qty,Condition` CSV, or `name,age`) fails with the parser's text: `Unrecognised header 'Item No,Color,Qty,Condition': expected Rebrickable columns Part, Color, Quantity (or part_num, color_id, quantity)`.
3. Import of the owner's Pileometer file (about 3 s). Import report: Format `rebrickable-parts-list`, Rows 388, Pieces 842, Spares 0, "Parts not recognised 17" with lines like `Unknown · colour 14 · ×7`, `Unrecognized · colour 4 · ×6`, `30237b · colour 4 · ×3`. Headline says "798 pieces in 225 distinct parts". Screenshot: `screenshots/j3-import-report.png`.
4. Parts lists tab: one card (name, file, "798 pieces", "225 distinct parts · 6 colours", orange "17 unrecognised" badge, View parts, Delete).
5. Inventory detail: summary line, search box ("Search part number or description"), colour dropdown (top 12 colours with counts), table Part / Description / Colour / Qty sorted by quantity, 50 per page. Search "wheel" finds 7 rows, "3001" finds 4. Search "red" finds nothing (colour names are not searched) and the table is just empty. At 390 px the page fits (no sideways scroll) but the search placeholder is cut off.
6. "What can I build?" tab. First open: filters appear immediately with "Ranking models…". The first scan of the 1,821 bundled models took 331 s here. After that each filter change takes about 6 s. Result: "1,565 of 1,821 models match; showing the best 30", best match 76% (Set 566-1 elephant, 26 of 34 pieces, 8 missing), then 73%, 72%, 69%, 68%. "Missing at most 25%" leaves 1 model, 5% leaves 0 ("No model matches these filters..."). Search "truck" gives 159 matches. Screenshot: `screenshots/j3-buildable-models.png`.
7. "Design something like this" on a card: goes to New chat with the prompt `Design something like this: A large blue elephant sculpture built entirely from the Animal submodel, with no direct pieces of its own.` and silently sets parts mode Prefer with all lists (stored in localStorage). The only visible sign is a red dot on the settings icon.
8. Chat settings popover, row "Parts": Off / Prefer / Only, one line of hint, inventory checkboxes. Fine and findable.
9. Result (recorded Only-mode red-speedster chat): after "Published model" a card "Parts check · 10 short · Only my parts", summary "40 of 50 pieces owned; short by 10 piece(s) across 3 part/colour line(s), 3 part(s) not owned at all.", a table of 3 parts (3641, 4624, 4600, all Black, own 0). The very next assistant paragraph says the underframe "uses these red parts, all from your scanned inventory". Screenshot: `screenshots/j3-parts-check-card.png`.

## Friction points

**F3-1. The parts check is a dead end and contradicts the agent (high).**
What the user sees: in an Only chat the card says 10 short, then the agent says everything is from the inventory. The card offers no action. The three missing parts are plain part numbers (3641, 4624, 4600) with no picture and no hint that the user owns other parts that could stand in. There is no "ask the agent to fix this" button, and no "where to get them" link.
Why it matters: the owner's whole reason for Only mode is a buildable model. A red "short" card plus a text that says the opposite is the most trust-damaging moment of the journey. The system prompt tells the agent to say so ("never present the model as complete...") but the transcript shows it did not.
Evidence: `components/PartsCheckCard.tsx` (renders `short` rows only; no substitution data, no action), `screenshots/j3-parts-check-card.png`, recorded chat `20261022-170345-ad9df6` (two cards, both 10 short in mode only). The check payload has no colour-substitution fields at all (`parts_check.short[]` is part, colour, needed, owned, missing).

**F3-2. Colour substitutions are only a percentage on the buildable cards (high).**
What the user sees: a checkbox "Also show the share if other colours may stand in" and a muted line "x% if other colours of the same part may stand in". In the parts check, and in the editor, there is no substitution at all. The user cannot see which owned colour would replace which missing one.
Evidence: `components/parts/BuildablePanel.tsx` lines with `substitute`; `PartsCheckCard.tsx` has none; `routers/editor.py::_parts_table` has none. With the owner's inventory the checkbox changed nothing visible on the top card (no gain line was shown), so the user cannot tell whether the feature works.

**F3-3. "What can I build?" shows stale results with no busy state, and each change takes about 6 s (medium).**
What the user sees: change "Missing at most" or type in Search, nothing happens for 6 s, then the list jumps. The old list stays, no spinner, because the panel keeps the previous `ready` state while loading (`setPhase(p => p.kind === "ready" ? p : ...)`). In my first run this made a filter look broken (still showing the elephant after choosing "truck").
Evidence: `BuildablePanel.tsx` (the effect around `inventoryApi.buildable`); request log: ~6-7 s per `GET /api/inventories/buildable`.

**F3-4. The default view hides the answer to the question the tab asks (medium).**
With 798 pieces nothing is more than 76% covered. The default filters ("Any amount", min 20 pieces) list 30 models between 76% and about 60%, each with a long "missing" list. Nothing tells the user "nothing is close to buildable yet; the best is 76%", and nothing suggests what to do (prefer mode, design something). A 34-piece elephant at the top is also a small model that is mostly one colour; ranking is purely by percentage.
Evidence: `screenshots/j3-buildable-models.png`; `BuildablePanel.tsx` (`SHARES` default `""`, `minParts` default "20").

**F3-5. Cards have no picture and use machine-written sentences as titles (medium).**
What the user sees: the card title is the model description, for example "A large blue elephant sculpture built entirely from the Animal submodel, with no direct pieces of its own." or "Name: 435 - Tipper Truck.ldr". The set id and name are in 11 px grey text under it. No thumbnail, so "which is the windmill" requires reading. The same sentence becomes the chat prompt in "Design something like this".
Evidence: `BuildablePanel.tsx` `Card` (`title = model.description || model.id`), `design()`; screenshot above. Open question for the fix: whether reference models have PNGs available to the backend (the generated gallery does, `generated/*.png`; the bundled references were not checked).

**F3-6. Only Rebrickable CSV is accepted, and the page says so in three different ways (medium).**
Dialog copy says "Rebrickable format"; My Parts says "a Rebrickable CSV"; the empty state adds "or any tool that writes Part, Color and Quantity columns". Both Rebrickable dialects (parts list, `part_num,color_id,quantity` database) are handled in `ldraw_tools/inventory.py`. BrickLink exports (XML, or CSV with `Item No`/`Qty`) and BrickLink colour names are rejected with the raw header error. A user with a BrickLink wanted list sees an error that lists column names they cannot map. No example file or "download a template" link.
Evidence: tested files `bl.csv`, `bad.csv`; `inventories.py::import_csv`; `ldraw_tools/inventory.py` line 212.

**F3-7. Import report numbers do not reconcile and placeholder rows are explained wrongly (medium).**
Report: "Pieces 842" but "now has 798 pieces". The 44 pieces are the unmapped rows, but the report never says so. Under "Parts not recognised" the hint says "These Rebrickable parts have no LDraw equivalent", but most lines are `Unknown` / `Unrecognized`, which are Pileometer's own placeholders for pieces it could not identify (not Rebrickable parts at all). `30237b` is a real part that fails to map and is listed the same way as the placeholders. The list shows 10 lines with "Show all 17". Colour is shown as a number ("colour 14"), not a name.
Evidence: `components/parts/ImportReportView.tsx`; `screenshots/j3-import-report.png`.

**F3-8. Inventory browsing is thin (low).**
No part images, so "Brick 1 x 2 with Grille" must be read. Search does not match colour names ("red" returns an empty table with only "No parts match this search"). The colour dropdown shows only the 12 largest colours. No sort. No sum per part across colours. Search box placeholder is clipped at 390 px.
Evidence: `components/parts/InventoryDetailView.tsx`; `inventories.py::detail` (search matches part id and description only).

**F3-9. No way to update a list, and deleting or replacing one silently breaks chats (medium).**
Uploading a new export under the same name returns "An inventory called ... already exists" (409), so updating means Delete then Upload, which loses the id. The Delete confirm says "Chats that use it will no longer steer towards these parts", but the chats keep `parts_mode: "only"` with a dead id: in the editor every part then reads "Not in inventory" and the "From my inventory" toggle returns HTTP 400 with no explanation (I hit this by accident during the run).
Evidence: `inventories.py::import_csv` (`_name_taken`), `routers/editor_palette.py` 400, `Parts.tsx::remove`.

**F3-10. First scan of the bundled models is only started when the user asks (low-medium).**
331 s of waiting on the first visit to the tab. `buildable.warm()` exists but is only called from the retry button and the first query, not at server start nor after the first import. I only saw "Ranking models…" for the first moments; the "Scanning ... few minutes" progress view appears once the backend answers with its status, which I did not capture on screen.
Evidence: `backend/buildable.py::warm`, `BuildablePanel.tsx::retry`.

**F3-11. Parts mode is almost invisible once chosen (low).**
New-chat from "Design something like this" sets Prefer silently. In the composer the only trace is a red dot on the settings icon; the chat header shows nothing about parts mode or which list. Opening the chat later gives no cue that it is an Only chat until a card appears.
Evidence: `Composer.tsx` (`nonDefault` dot), `screenshots` not kept (settings popover looked fine); `BuildHeader.tsx` has no parts field.

---

# Journey 4: agent plus manual editing

## Steps as the user experiences them

1. Chat with an agent-built model (starship chat, 31-part model card). Header: title and a small red "Edit in 3D" text link at the far right.
2. First click opens `/chat/:id/edit` and, because the chat has no editor session yet, the start panel: "Which model do you want to edit?" and two buttons labelled with raw file paths (`azure-dart.plan.json`, `bench/azure-dart.plan.json`) with grey sub-text "31 placements · 1 section · Azure Dart - small white and blue starfighter" and "57 placements · 7 sections · ...". Plus "Start a new model". Screenshot: `screenshots/j4-open-plan-choice.png`. It does offer the chat's plans, as intended.
3. Editor: toolbar Select / Move / Rotate, Undo, Redo, Delete, Group, Publish, "Open plan…", a fit-view icon. Palette on the right. Status line bottom-left: "Revision 1 · 51 parts used, 18 beyond what you own".
4. Select: click a part. Status: "1 selected · 3068b.dat in sub-model az-skin.ldr · 1 of 2 under the pointer, click again for the next". Clicking again cycles to the part behind (78444 in az-nose). Parts inside sub-models are selectable.
5. G (or Group): selection becomes "sub-model az-hull.ldr (the whole group)" and the orange box covers the whole sub-model.
6. Keyboard on the group: ArrowRight, PageUp, Shift+ArrowLeft each applied one edit (one stud right, one plate up, a quarter turn). Shift+ArrowUp does nothing and says nothing. Undo (button or Ctrl+Z) steps back through them; at the start Undo disables. Screenshot after nudging: `screenshots/j4-group-nudged.png` (hull moved, rotated and visibly detached from the rest, no warning).
7. Move tool: large red/green/blue arrows appear at the part; dragging the blue arrow moved the part (revision +1). Rotate tool: three big rings.
8. Palette: type "3001", pick White, click Add. Search takes about 5 s per request. "2x4" returns nothing and shows nothing (no "no matches" line); "brick 2 x 4" returns Brick 4 x 6 first and Brick 2 x 4 fourth. Add on 3001 White (palette says "26 available · in White: 1") was refused with "The inventory cannot cover this edit". Screenshot: `screenshots/j4-palette-refused-published.png`.
9. Publish: status line reads only "Published (6f83577159b6)."
10. "Back to the chat": the transcript has no new message (64 messages before and after). A second model card appears at the very bottom, titled `azure-dart-v1.mpd`, "51 parts", "2 warnings", "just now", with the plan's description. Screenshot: `screenshots/j4-chat-after-publish.png`. There is no parts-check card for it.
11. Continue with the agent (no model could be configured, so this part is read from code): the next turn gets the generic system-prompt line about the editor tools. Nothing marks that the person edited or published.

## Friction points

**F4-1. The chat does not record the person's edit or publish (high).**
What the user sees: after Publish the only trace in the chat is a card at the end, named after the file (`azure-dart-v1.mpd`), with no "edited by you" label, no link to the plan it came from, and no parts check. The agent's earlier text still describes the old model. A later reader cannot tell that card from an agent publish.
Why: `routers/editor.py::editor_publish` calls `tools.t_publish_model` outside a turn, so the parts check it computes is returned in the HTTP response (`parts_check`) and then ignored by `viewer/editor.js` (it only prints the model id); no message is appended to `messages.jsonl`.
Evidence: `screenshots/j4-chat-after-publish.png`; `editor.py` lines ~335-338; `editor.js` publish handler; message count unchanged at 64.

**F4-2. The agent cannot actually continue from the person's edit (high).**
What happens: `editor_state` returns revision, piece and kind counts, bounds, tightest available parts and shortages only. It lists no part ids and no diff, no plan path, and nothing says what the person changed or when. `editor_edit` needs ids (`move{id,...}`), which the agent has no tool to learn. The system prompt does not say where the working plan is (`output/editor/plan.json`), so the agent cannot read it with `read_file` unless it guesses. Nothing is injected at the start of a turn when the revision moved since the agent's last look. Also, once a chat has an editor plan, `editor_open` is hidden from the agent (`tool_policy.available_tools`), and `t_editor_open` never passes `replace`, so after the person edits, the agent cannot swap in a rebuilt plan; if it regenerates `azure-dart.plan.json` from its generator the person's edit is not in it.
Why it matters: "Agent plus manual" is a loop; today the second half (agent builds on the person's edit) works only by accident.
Evidence: `editor_tools.py` (`_summary`, `t_editor_open`, `OPEN_TOOLS`), `tool_policy.py` line 24, `prompts/system.md` line 29. Not exercised live (no model).

**F4-3. Publish gives almost no feedback and skips the parts check (high).**
Status text "Published (6f83577159b6)." is the model's internal id in 12 px grey text at the bottom-left; no link to the chat card, viewer or download; no summary of what was published (51 parts, 13 beyond what you own). The model was published although the plan was 13 parts beyond the inventory in an Only chat. In the chat the agent's rule (publish_model repeats the check and reports) does not apply to the editor path.
Evidence: `viewer/editor.js` publish handler; `screenshots/j4-palette-refused-published.png` (status line); `editor.py::editor_publish`.

**F4-4. The editor shows only a count of what is short, and a plan that is already short blocks every add with a vague message (high).**
The status says "51 parts used, 18 beyond what you own" (13 after I restored the inventory) but there is no list of which parts, no highlight in the 3D view, no link to the parts check. The plan came from outside the inventory, so in Only mode the first Add of an owned part (3001 White, "26 available · in White: 1") is refused with "The inventory cannot cover this edit": red text under the colour swatches, not naming the parts that are actually short, contradicting the "available" shown on the row. The palette also repeats itself on every non-owned row ("Not in inventory" and "Not in your inventory: not allowed in Only mode").
Evidence: `components/editor/Palette.tsx` (lines 82, 95-99), `editor.py::_parts_table` (the `short` list exists in the API and is unused by `editor.js::describeParts`, which only counts); screenshot.

**F4-5. Palette search is slow, returns nothing silently, and has no pictures (medium).**
About 5 s per request. "2x4" returns no rows and no message. Ranking puts Brick 4 x 6 and Brick 2 x 6 above 3001 for "brick 2 x 4". Rows are text only (no thumbnail), so adding "Panel 6 x 6 x 12 Corner Lattice" is a guess. Default colour is Red regardless of the selected part or the model (the starship is white and blue).
Evidence: `Palette.tsx` (`setItems([])` with no empty state; `useState(4)` default colour), `routers/editor_palette.py`; request log.

**F4-6. Selection feedback disappears after every edit (medium).**
Selection info ("1 selected · 3035.dat in sub-model az-hull.ldr · 1 of 3 under the pointer...") lives in the same 12 px status line as revision and part count. After any edit the line reverts to "Revision 4 · 51 parts used ..." while the orange selection box is still drawn (`screenshots/j4-group-nudged.png`). The status wraps to two lines and is easy to miss; it is the only place that names the selected part or tells you "click again for the next".
Evidence: `viewer/editor.js` line 305 vs 565.

**F4-7. Shortcuts are not discoverable and have gaps (medium).**
Arrows, Page Up/Down, Shift+Left/Right, G, Delete, Ctrl+Z exist only in tooltips (the Group button tooltip mentions G; nothing mentions arrows or Page keys). Arrows are world axes, not camera-relative (`editor-core.js::nudgeEdits`), so "right" on screen can be "back" in the model. Shift+Up/Down do nothing and give no message. Nudging a group can leave it detached (screenshot) with no connectivity warning. There is no shortcut sheet and no on-canvas hint at first open.
Evidence: `editor.html` tooltips, `editor.js` keydown handler, `editor-core.js` lines 363-373.

**F4-8. Open-plan list is paths and counts only (medium).**
Raw paths as button labels, no thumbnail, no mark for "this is the model published as card X in the chat", no date shown (the API returns `modified`). With two similar starfighter plans the choice is a coin flip. After choosing, "Open plan…" later replaces the session silently (old one is archived under `editor/archive/`, but the person is only told in small text before).
Evidence: `viewer/editor.js::showStart`, `screenshots/j4-open-plan-choice.png`.

**F4-9. Gizmo and framing are heavy, and phone is a dead end (low).**
Move and Rotate widgets are far larger than the selected part (a 1x2 plate gets arrows about 150 px long) and cover neighbours. The model fills about 40% of the canvas. On a 390 px phone the editor shows "Editing needs a desktop browser with a mouse. You can still look around." but still renders the whole palette (half the screen) which cannot be used; the chat's "Edit in 3D" link tooltip says "(desktop)" but is shown on phones too.
Evidence: `screenshots/j4-editor-phone.png`, `pages/EditorPage.tsx` (palette rendered whenever `bridge` exists), `ChatPage.tsx`.

**F4-10. "Revision N" is jargon and undo does not reduce it (low).**
Undoing three edits moved the label from Revision 4 to 5, 6, 7. Correct for conflict handling, wrong as a user-facing label.
Evidence: status text in the run; `editor.js` line 305.

## What worked (so it is not broken by fixes)

- The editor offers the chat's plan files and lists their size; opening plans from a chat folder works first try.
- Picking: clicking cycles through overlapping parts, sub-model parts are selectable, G widens to the sub-model, groups move as one.
- Keyboard nudges, undo/redo, and the gizmo drag all produced one edit each and updated the model without a reload (the 3 s poll also picks up agent edits).
- The palette's "From my inventory" toggle and per-colour "available" counts are the right idea.
- My Parts import, search and paging are fast and clear; mobile has no sideways scroll.

---

# Ranked fixes

Effort: S under a day, M a few days, L a week or more.

| # | Fix | Journey | Impact | Effort | Files |
|---|-----|---------|--------|--------|-------|
| 1 | Record the person's publish in the chat: append a message "You edited this plan and published it" with the model card and its parts check, and label the card (edited by you, from plan X). Make editor.js show a link and the parts summary instead of the id. | 4 | High | M | `backend/routers/editor.py` (editor_publish, store message), `backend/store.py`, `frontend/src/components/MessageList.tsx`, `ModelCard.tsx`, `viewer/editor.js`, `frontend/src/pages/EditorPage.tsx` |
| 2 | Give the agent the person's edit: inject a one-line note at turn start when the editor revision moved ("the person edited the plan: revision N, M parts changed, plan at output/editor/plan.json"); extend `editor_state` with a list of entries (id, part, colour, section) and a changed-since-last-call diff; let `editor_open` pass `replace`; mention the plan path in the system prompt. | 4 | High | M | `backend/editor_tools.py`, `backend/tool_policy.py`, `backend/prompts/system.md`, `backend/agent_loop.py` (turn-start note) |
| 3 | Parts check card: add colour substitution suggestions to each short row (owned colours of the same part), a "Ask the agent to fix this" button that sends a prefilled message, and make the card state the contradiction when the agent text claims otherwise (or keep the card but add a pinned warning). | 3 | High | M | `frontend/src/components/PartsCheckCard.tsx`, `backend/parts_steering.py` (check payload), `backend/tools.py::parts_check`, `MessageList.tsx` |
| 4 | Editor shortage view: list the short parts (click selects them in 3D), name the parts in the refusal ("needs 3641 Black, own 0"), and clear the contradictory palette wording. | 4 | High | M | `viewer/editor.js` (describeParts/status), `viewer/editor-core.js`, `frontend/src/components/editor/Palette.tsx`, `frontend/src/pages/EditorPage.tsx`, `backend/routers/editor.py` (error detail) |
| 5 | Buildable panel: show a loading state on every query, default to a useful filter or a headline ("best match 76%; nothing is close yet, try Prefer mode"), use set id and name as title, add thumbnails. | 3 | Medium-high | M | `frontend/src/components/parts/BuildablePanel.tsx`, `backend/buildable.py`, `backend/routers/inventories.py` |
| 6 | Pre-warm the buildable scan at server start and after the first import, and show progress on the My Parts page. | 3 | Medium | S | `backend/buildable.py`, `backend/routers/inventories.py`, `backend/main.py`, `BuildablePanel.tsx` |
| 7 | Editor search and palette: empty state ("no match, try a number"), looser match for "2x4", better ranking (exact id and "brick N x M" first), part thumbnails, default colour from the selected part or model, and a faster endpoint. | 4 | Medium | M | `backend/routers/editor_palette.py`, `frontend/src/components/editor/Palette.tsx`, `paletteLogic.ts` |
| 8 | Editor feedback: keep the selection line separate from the revision line (two lines or a small selection chip), drop "Revision N" for "Saved", add a shortcuts popover and a first-open hint, camera-relative arrows or a note, a message for Shift+Up/Down. | 4 | Medium | M | `viewer/editor.js`, `viewer/editor.html`, `viewer/editor.css`, `viewer/editor-core.js` |
| 9 | Import honesty: one sentence on supported formats with an example file; show BrickLink as unsupported with a pointer; reconcile the report ("842 read, 44 not matched, 798 usable"); explain Pileometer `Unknown`/`Unrecognized` rows; colour names instead of numbers; refresh the Name when another file is chosen. | 3 | Medium | S | `frontend/src/components/parts/UploadDialog.tsx`, `ImportReportView.tsx`, `pages/Parts.tsx`, `backend/inventories.py` (report), `ldraw_tools/inventory.py` (optional BrickLink dialect later, L) |
| 10 | Replace a list in place (keep the id), and when a list a chat depends on is deleted, show a banner in that chat ("its parts list is gone, mode is now Off") and in the editor. | 3 | Medium | M | `backend/inventories.py`, `backend/routers/inventories.py`, `pages/Parts.tsx`, `backend/parts_steering.py`, `ChatPage.tsx`, `EditorPage.tsx` |
| 11 | Open-plan list: thumbnail or part count, relative date, a mark for the published model; show the chat card name. | 4 | Low-medium | S | `viewer/editor.js` (showStart), `backend/routers/editor.py::plan_files` |
| 12 | Inventory detail: search by colour name, empty-state hint, thumbnails, sort, all colours in the dropdown, fit the search placeholder at 390 px. | 3 | Low-medium | S | `frontend/src/components/parts/InventoryDetailView.tsx`, `backend/inventories.py::detail` |
| 13 | Show parts mode and list name in the chat header or composer chip. | 3 | Low | S | `frontend/src/components/BuildHeader.tsx`, `Composer.tsx` |
| 14 | Phone: hide the palette and the "Edit in 3D" link below a width, or say "desktop only" up front. Smaller gizmo scale for small selections. | 4 | Low | S | `frontend/src/pages/EditorPage.tsx`, `ChatPage.tsx`, `styles/chat.css`, `viewer/editor.js` |

## Which fixes can be built in parallel

Independent (touch disjoint files, safe to run at once):
- A: fixes 1 and 2 are one chat-integration stream but they touch different files; 1 (publish record, `editor.py`, `MessageList`, `ModelCard`) and 2 (`editor_tools.py`, `tool_policy.py`, `system.md`) can be separate workers if both coordinate on the message format only. They both edit `routers/editor.py` lightly (publish vs error detail), so merge conflicts are possible in fix 4.
- B: fix 3 (parts check card, `parts_steering.py`, `PartsCheckCard.tsx`).
- C: fix 5 and 6 together (buildable panel and backend `buildable.py`).
- D: fix 9 and 12 together (My Parts import and detail UI).
- E: fix 8 and 14 together (`viewer/editor*.js|css|html`, `EditorPage.tsx`); do not run beside 4 or 11, which also edit `viewer/editor.js`.
- F: fix 7 (palette, `editor_palette.py`); fix 13 (header chip); fix 10 (inventories storage).

Must be sequenced:
- 4 and 11 and 8 all edit `viewer/editor.js`: do 4, then 11, then 8 (or give one worker all three).
- 1 and 3 both want a card in `MessageList.tsx`: build 3 first (smaller), then 1.
- 10 changes the inventory id contract that 3 and 4 read; start it after them or agree the "list missing" state first.
- 2 depends on nothing; start it first, it is the most valuable and the least visible.

## Suggested first batch (highest value for the least coupling)

Fix 1 + 2 (chat knows about the edit, agent can follow it), fix 3 (the check card), fix 5 + 6 (buildable page), fix 9 (import honesty). Everything else can wait.
