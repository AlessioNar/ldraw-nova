# Journeys 5 and 6: My Collection and Cost Control

Audit only; no product code changed. Real browser (Chrome via Playwright, swiftshader) against an isolated container built from this worktree, at 1440 px and 390 px, with the owner's recorded chats, models and outputs copied in read-only (3 chats, 9 models).

What is real and what is mocked:
- Real: My Models page, 3D View, 3D Player, downloads/delete dialog, the finished starship chat (EUR 1.50 of 2.00), the model card in a chat, the cost panel, the budget settings panel, the New chat page.
- Mocked with `page.route()`: no API keys exist, so `/api/llm-models` returned two fake priced entries (an Opus-class and a Haiku-class model) so the pickers could render; the "running at 85%" and "budget reached" states are the real starship chat detail with `usage.cost_eur` and the notice message edited (1.72 / 2.03). The running stream mock closes immediately, so its header shows "Reconnecting to live updates"; that is an artefact of the mock, not a finding. The "many models" run duplicated the 8 gallery entries into 48 cards (models with `-v1..-v6` names); the full-page screenshot is not kept because the page scrolls inside an inner container.
- No model call was made.

Screenshots (in `screenshots/`): `j5-models-d.png`, `j5-models-m.png`, `j5-player-m.png`, `j6-cost-panel-d.png`, `j6-settings-budget-d.png`, `j6-running-85pct-d.png` (mock), `j6-budget-reached-d.png` (mock), `j6-budget-reached-m.png` (mock).

---

## Journey 5: My Collection

### Steps as the user experiences them

1. Click My Models in the sidebar. A grid of cards appears (4 across at 1440 px, 1 at 390 px), newest first, with a spinner line "Processing N models" while snapshots/BOMs render.
2. Find a model: the only tools are one text filter ("Filter by name, description or chat") and the sidebar History list. No sort, no grouping, no chat/version filter, no pagination.
3. Each card: snapshot, a bold file name (ellipsised), part count, description, "1 h ago · from <chat title>" (link), then 3D View, 3D Player, and .mpd / .glb / BOM downloads, and a round x delete at top right.
4. 3D View / 3D Player (also by clicking the thumbnail) open a modal with a switch between "3D view" and "3D player"; the player auto-plays the build step by step with a timeline, "Step 2 / 4", speed and fit controls. An "Open in new tab" link is in the header.
5. Delete: x opens a browser `confirm()` ("Delete <file> and its preview, parts list and notes ... This cannot be undone. Original chat work files are kept.").
6. Back to the chat: click the red chat title in the card footer (it is a link to `/chat/<id>`), then continue by typing in the composer. Inside a chat the same card appears under the answer (`#model-<id>`), with a "Jump to result" button in the header and an "Edit in 3D" link at the top.

### Friction points

| # | Friction | What the user sees | Why it matters | Evidence |
|---|---|---|---|---|
| F1 | Versions are invisible | A chat that produced `red-speedster-v1` and `red-speedster-no-2x12-plate-v1` shows two near-identical cards; the two Tidal Observatory models (2,205 and 2,734 parts) show the same description and truncated names `the-tidal-observatory-...`. "-v1" is only a file-name suffix; there is no version number, "newer than", diff, or grouping. With 48 cards (6 versions of 8 models) there are 12 rows of look-alikes. | The owner iterates on models; the collection grows by near-duplicates and the user cannot tell which is the latest or best, or what changed (parts delta, cost). | `j5-models-d.png`; `app/web/frontend/src/pages/Models.tsx` (flat `shown` list, filter only), `ModelCard.tsx` |
| F2 | No sort, no view options | Fixed newest-first order from the backend (`gallery.collection`, sorted by mtime). Cannot sort by parts, name, cost, or chat; no list view. | Finding "the big one" or "the cheap one" means scrolling a grid of 250 px cards; at 48 models that is many screens, no virtualisation (lazy images only). | `Models.tsx`, `backend/gallery.py` line 142 |
| F3 | File names are the title | The bold title is the file name, ellipsised ("azure-dart-small-white-b...", "red-speedster-no-2x12-p..."); the human name ("Azure Dart - small white and blue starfighter") is a grey description below. Names that share a long prefix are indistinguishable. | The most scannable line is the least informative one; at 390 px it truncates after ~28 characters. | `j5-models-d.png`, `j5-models-m.png`; `ModelCard.tsx` lines 80-86 |
| F4 | "Back to the chat" is a small link | The chat link is red text inside a muted line ("1 h ago · from Please design a small whit..."), truncated by ellipsis; several chats are comma separated. There is no "Continue in chat" button, and the card shows nothing about that chat (cost, state, number of versions). | The natural next action after finding a model is "tweak it", and it is the least visible control on the card. | `ModelCard.tsx` lines 97-110 |
| F5 | Delete is a native confirm with no undo; the deleted state is confusing | `confirm()` text mentions "preview, parts list and notes" but not that the model also disappears for the chat card. The chat card then shows "This model was deleted from My Models" on the thumbnail. Delete button sits in the card corner next to the thumbnail with no confirmation in-app (blocked by browsers that suppress dialogs). No bulk delete. | Deleting the loser of five versions is a five-click, five-dialog job; mis-click risk because the x is at the thumbnail corner. | `ModelCard.tsx` lines 34-40, 62-66; `routers/models_gallery.py` delete |
| F6 | Downloads are cryptic icons and slow glb | ".mpd .glb BOM" with identical download icons; "BOM" means nothing to a casual user; .glb may convert for up to a minute (button spins, hint only in the tooltip). "Download all (.zip)" has no per-chat or selection variant and no size hint. | New users do not know which file to pick (.mpd to open in LDraw/Studio, .glb for 3D tools, BOM = parts list CSV). | `ModelCard.tsx` lines 128-161 |
| F7 | 3D Player: autoplay and mobile header | The player starts playing immediately; steps come from the MPD (31 parts in 4 steps), so the "step-by-step instructions" are 4 big jumps for a 31-part model, no per-step parts list. At 390 px the modal header wraps ("Open in new tab" breaks over 3 lines) and the title is cut to "Azure Dart - s...". | Instructions are the point of the Player; 4 steps for 31 parts do not read as build instructions. The player cannot be printed or exported. | `j5-player-m.png`; `ViewerModal.tsx` |
| F8 | Warnings badge vague | A yellow "1 warning" badge appears on every model card in chats (the same canned text: "Physical buildability is not proven; read the validation and visual review reports."). | A warning that is on every model carries no information and trains users to ignore the badge. | `j5-...-card` behaviour; `models.jsonl` of all 3 chats |
| F9 | Cost and provenance missing on the card | Cards show date and part count but not what the model cost, which LLM made it, or how long it took, although the chat knows all three (`usage.cost_eur`, `duration_s`). | Cost is the owner's main constraint; the collection is the natural place to compare "2 EUR for 31 parts" with "0 EUR for the gallery model". | `ModelCard.tsx`; data in `GET /api/chats/<id>` `usage` |
| F10 | Mobile: one long column, filter and zip stack | At 390 px each card is ~410 px high, so 48 models are ~20k px of scrolling; filter and "Download all" take two rows above the first card. | Browsing on a phone is a long scroll with no jump-to. | `j5-models-m.png` |

What works well (keep): the filter also searches chat titles and descriptions, snapshots/BOMs render in the background with a clear "Processing N models" line, 3D View/Player are one click from a card (and from the thumbnail), the in-chat card has the same actions plus "Jump to result", model cards in the sidebar show 4 most recent thumbnails per chat.

---

## Journey 6: Cost Control

### Steps as the user experiences them

1. New chat. The composer shows the model picker ("Claude Opus"), a "Budget EUR 2" chip (default), and a settings button. The four example prompts carry no cost or time hint (`new-d` screenshot, not kept).
2. Optional: click the Budget chip, a settings popover opens focused on the Budget select (No budget / 1 / 2 / 5 / 10 / 20 / Custom). Workers row shows the worker model with "$1 in · $5 out per 1M tokens". Permissions "Full access" says "Needed for workers." (`j6-settings-budget-d.png`).
3. Model picker rows show "$15 in · $75 out per 1M tokens" per model (dollars, per million tokens).
4. Press Send. While it builds, a sticky strip shows "Step 23 / ~N" (the ~N appears only after 3 finished chats), the phase, a clock, the cost "EUR 1.72 / EUR 2.00" with a bar that turns amber at 80%, "1 worker (1 running)" and Stop (`j6-running-85pct-d.png`, mock).
5. Click the cost number for the breakdown: tokens in/out/cached and "Estimated from the models' list prices at 0.92 EUR per USD" (`j6-cost-panel-d.png`).
6. At the limit the turn stops with a grey line in the transcript: "Budget reached: about EUR 2.03 of EUR 2.00 spent in this chat. Raise the budget in the chat box and send a message to continue." The header says "Done" in green next to a red EUR 2.03 (`j6-budget-reached-d.png`, `-m.png`, mock).
7. To continue: click "Budget EUR 2" chip, change the select to a larger value, type a message, send.
8. Past costs: only visible inside each chat's header after opening it. The sidebar and My Models show no cost anywhere; there is no total.

### Friction points

| # | Friction | What the user sees | Why it matters | Evidence |
|---|---|---|---|---|
| C1 | Nothing says what a build will cost before Send | The budget chip is a cap, not an estimate. Prices are "$ per 1M tokens". The real data: a 31-part starfighter cost EUR 1.50 (27 model steps, 1.34M tokens in, 4 min of the last turn) - 75% of the "medium model" target for a tiny model. The user cannot tell that from any screen. | The owner's target is about 2 EUR per medium model; the user is flying blind until the bar moves. | `Composer.tsx` lines 173-176, `ModelPicker.tsx` `priceLabel`, real chat 20261023-174459-0526d4 |
| C2 | Budget is per chat, not per request | Backend `turn.py` `before_step` compares the whole chat's stored cost with the budget. After a EUR 1.50 first model, a small follow-up tweak has only EUR 0.50 left and stops. The chip says "Budget EUR 2" with the tooltip "when this chat has cost about this much". | Iterating (the normal workflow) silently burns the same cap; users will think "I set 2 EUR per model". There is no "EUR X more for this message" control. | `backend/turn.py` lines 33-42, `Composer.tsx` tooltip |
| C3 | Budget-reached notice is passive and the header says "Done" | A small grey line (same style as any notice), in the transcript just above the composer; at 390 px it is half hidden behind the composer. The header shows a green "Done" (it did not finish; it was cut off) next to a red cost. No button to raise the budget, no "continue with +EUR 2" action, no statement of what is complete (is the model usable? what is missing?). The Send button looks the same; the chip does not turn red. | The user must discover that the fix is the small chip, change a select, then type something to resume. Nothing says that the unfinished work will continue from where it stopped. | `j6-budget-reached-d.png`, `j6-budget-reached-m.png`; `BuildHeader.tsx` (only "Done" branch for `!running`), `turn.py` message text |
| C4 | Header mixes this-turn and chat-total numbers | "Done · 4 min · EUR 1.50 / EUR 2.00 · 3 steps": the minutes and steps are for the last turn (`turn_steps` 3, `duration_s` 243) but the cost is for the chat (27 steps). The panel then lists 1.34M tokens (chat total). | The reader concludes that 3 steps cost EUR 1.50. | `j6-cost-panel-d.png`; `BuildHeader.tsx` lines 33-37, 60-64 |
| C5 | Amber bar is the only 80% signal, and it is subtle | A thin bar goes amber with `near`; no text, no toast, no "about N steps left at this rate". At 390 px the bar is a 40 px stub. | The warning arrives with a few cents left, in a place the user is not looking while a long turn runs. | `j6-running-85pct-d.png`; `BuildHeader.tsx` lines 41, 52 |
| C6 | Past costs: none per chat or in total | The sidebar History shows title and "1 h ago" only; My Models shows no cost. Two of the three recorded chats show no price at all: `unpriced_steps: 38`, `cost_eur: 0` while having used 3.0M and 2.0M prompt tokens, so their headers read "EUR -" (the model had no price when they ran). | The owner cannot see the month's spend, or which chat was expensive, or compare models; unpriced history looks free. | `GET /api/chats/<id>` usage for the three chats; `Sidebar.tsx`; `BuildHeader.tsx` (`euro = priced ? ... : "EUR -"`) |
| C7 | Euros are estimates from an unreachable setting | The rate (0.92) is only changeable through the environment variable `LDRAW_NOVA_EUR_PER_USD`; it is not in Settings. The panel's sentence "Estimated from list prices at 0.92 EUR per USD" is the only mention. Picker prices are in dollars, the budget in euros, with no conversion in the picker. | The "editable rate" is not editable by a user in the UI; mixed currencies on the same screen. | `backend/settings.py` `eur_per_usd`, `UsageBadge.tsx`, `ModelPicker.tsx` |
| C8 | Model prices do not say what a model costs per model built | Rows show "$15 in / $75 out per 1M tokens"; no typical cost per build, no "cheapest" marker, no worker-ratio hint. Models without a price show "price unknown" and cannot take a budget (backend raises "This model has no known prices, so a budget cannot be enforced"). | The picker is the main cost lever and it speaks tokens. A 15x price difference is invisible as euros. | `j6-...picker` (chat-d-picker, not kept), `ModelPicker.tsx` 9-14, 84; `model_catalog.py` line 108 |
| C9 | Workers are invisible until they run | The Workers row exists only in the settings popover (a worker model is chosen by default; "Full access" is needed for workers). The rule that workers are only used for models over about 40 parts lives only in the system prompt (`system_prompt.py` line 45), so a user who selects a worker model and builds a 31-part ship gets EUR 0.00 of worker cost with no explanation. If permissions are "Ask first", workers are silently unavailable; the hint text appears only under the permission select. | The user cannot tell whether the cheap model will be used and whether it actually saved money; the panel's "Planner EUR x . workers EUR y" line appears only when workers cost something. | `ChatSettings.tsx`, `UsageBadge.tsx`, `system_prompt.py`, real starship chat (worker_cost_eur 0.0 with a worker configured) |
| C10 | Settings dot with no explanation | The settings button shows a red dot "changed from defaults" with no hint of which setting differs. | In a cost-conscious flow the user wants to see "Full access, workers on, budget 2" at a glance. | all chat screenshots (red dot on the sliders button) |
| C11 | No stop-on-budget before a long turn begins | The check runs between steps; a single step with a large prompt can overshoot (2.03 of 2.00 in the mocked example, and real turns re-send 1M+ tokens each step with cache). The notice then states "about EUR 2.03". | Minor, but the cap is soft, and users read "2" as hard. | `turn.py` `before_step`, `budget.py` |

---

## Fixes, ranked by impact and effort

Effort: S = under a day, M = a few days, L = a week or more.

| Rank | Fix | Effort | Files | Fixes |
|---|---|---|---|---|
| 1 | Pre-send cost line under the composer: "Estimated EUR 0.50-2.00, about N min" (heuristic from the last finished chats' median and the chosen model's price), plus "Budget EUR 2 for this request" | M | `Composer.tsx`, `BuildHeader.tsx` (`estimateSteps`), `ModelPicker.tsx`, small backend endpoint or client-side from `chats[].usage` | C1, C8 |
| 2 | Make the budget-reached state actionable: a banner with "Continue with +EUR 2" and "Raise budget" buttons, red chip, header label "Stopped: budget reached" instead of "Done", keep the composer message focused | S-M | `MessageList.tsx` (render `_notice` with an action), `BuildHeader.tsx`, `Composer.tsx` (chip class + a callback to set budget and send "continue"), `turn.py` (notice carries `{"_action":"raise_budget"}`) | C3, C5 |
| 3 | Budget per request (or "this much more"), shown in the chip: "EUR 2 for this message; EUR 1.50 spent in chat" | M | `turn.py` `before_step`, `budget.py`, `ChatSettings.tsx`, `Composer.tsx` | C2 |
| 4 | Show cost per chat in the sidebar and a total on My Models / Settings ("EUR 3.20 this month"); show per-model cost and builder model on cards; flag unpriced chats as "price unknown" instead of "EUR -" | M | `Sidebar.tsx`, `ModelCard.tsx`, `Models.tsx`, `api.ts` (`Chat.cost_eur`), `routers/chats.py` list endpoint, `usage.py` | C6, F9 |
| 5 | Version grouping on My Models: group by chat, show "v2 of 3", newest first with an expand; show parts delta against the previous version | M | `Models.tsx`, `ModelCard.tsx`, `routers/common.py` (`model_chat_index`), `gallery.py` | F1, F2, F3 |
| 6 | Sort and filter controls: sort (newest, parts, name, cost), filter by chat, "show only latest versions"; page or virtualise past about 24 cards | S-M | `Models.tsx` | F2, F10 |
| 7 | Card title from the human name (`info_heading`/description first line); file name as small caption; "Continue in chat" button | S | `ModelCard.tsx` | F3, F4 |
| 8 | Header clarity: label "this turn: 4 min, 3 steps" and "chat total: EUR 1.50, 27 steps" separately; stop mixing | S | `BuildHeader.tsx` | C4 |
| 9 | Worker transparency: show "Workers: Haiku, used for models over about 40 parts" in the composer/settings and a "workers not used (small model)" line in the cost panel; warn when "Ask first" blocks workers | S | `ChatSettings.tsx`, `UsageBadge.tsx`, `BuildHeader.tsx` | C9 |
| 10 | Euro-first prices and an editable rate in Settings; picker rows show a typical EUR per medium model | S-M | `ModelPicker.tsx`, `Settings.tsx`, `routers/environment.py` or a new settings endpoint, `settings.py` | C7, C8 |
| 11 | Player: per-step parts list, step count that follows sub-assemblies (not only MPD steps), pause on open, wrapping header at 390 px | M | `ViewerModal.tsx`, `app/web/viewer/*` | F7 |
| 12 | In-app delete dialog with undo toast, multi-select delete, readable download labels ("Parts list (CSV)", "LDraw file", "3D file") | S-M | `ModelCard.tsx`, `Models.tsx` | F5, F6 |
| 13 | Replace the constant "1 warning" with a model-specific check result or drop the badge when it is the canned text | S | `ModelCard.tsx`, backend `models.jsonl` writer | F8 |

### Independence for parallel work

Independent (no shared files): fix 8 (BuildHeader label only), 9, 10, 11, 12, 13. Fix 7 and 6 touch different files from 5 only partly: 7 (`ModelCard.tsx`) and 6 (`Models.tsx`) can proceed in parallel, but 5 changes both and should follow them or be done as one stream with 5, 6, 7. Fix 4 (cost per chat in lists) shares `ModelCard.tsx` with 5 and 7, so put them in one stream. Fixes 1, 2 and 3 all touch `Composer.tsx`/`BuildHeader.tsx` and the budget model, so build 3 first (the semantics), then 1 and 2 in parallel on top; 8 touches `BuildHeader.tsx` and should be merged with 2.

Suggested streams: (A) budget semantics and UI: 3 -> 2, 8 -> 1; (B) collection: 7 -> 6 -> 5 and 4's card part; (C) cost visibility: 4's sidebar/total part, 9, 10; (D) viewer: 11; (E) safe polish: 12, 13.

---

## Non-LLM paths (owner direction: deterministic first, LLM at the margin)

What the user experiences today: every request, including "red car with four black wheels", goes to an LLM agent (27+ steps, 1.3M tokens, about EUR 1.50 for a 31-part ship); there is no free path and no indication that one could exist. Only user experience is described; the pipeline design is another agent's job.

Ranked:

1. **Show the path before Send** (M, high impact). Under the composer a one-line verdict updates as the user types or picks an example: "Instant, free: matches reference model 'red speedster'" / "Parametric: 2x4 house, set width and height (free)" / "AI build, about EUR 1-2, about 4 min". The Send button label changes to "Build (free)" or "Build with AI (about EUR 1.50)". Files: `Composer.tsx`, `Home.tsx` (examples), a small match endpoint. Solves C1.
2. **Make the free path the default, the LLM an explicit step** (M). Ordering of the primary action: library match or generator first; "Ask AI to improve it" as a secondary button on the result. The budget chip only appears for LLM builds. Files: `Composer.tsx`, `ChatSettings.tsx`, `ChatPage.tsx`.
3. **Example prompts that map to free paths, with badges** (S). The four examples on New chat show "free" / "about EUR 1.50" badges; the "red car" example would resolve to an existing model in My Models or a gallery model. Files: `Home.tsx`.
4. **Reuse before regenerate** (S-M). When a prompt resembles a model in My Models ("a red speedster"), offer "Open existing 'red-speedster-v1' (48 parts)" and "Make a variation (free: recolour, change size) / with AI". This also reduces near-duplicate versions (F1). Files: `Composer.tsx`, `routers/models_gallery.py` search endpoint, `Models.tsx`.
5. **Follow-ups through the editor or parameters, not the agent** (M). After a result, show quick actions on the card: "Change colour", "Make it wider", "Remove part", "Add a window", opening the 3D Editor (`/edit` already exists: "Edit in 3D") or a small form. The user sees "free, instant"; only free-text requests go to the LLM with an estimate. Files: `ModelCard.tsx`, `EditorPage.tsx`, `ChatPage.tsx`.
6. **Cost label on every result and in the collection** (S). Each card says "Free (generator)" or "AI, EUR 1.50, 4 min", so the owner learns which paths pay off and the total spend (fix 4) splits into free vs AI builds. Files: `ModelCard.tsx`, `Sidebar.tsx`.
7. **Budget notice offers the free alternative** (S). When the budget stops an AI build: "Budget reached. Finish in the editor (free), continue with +EUR 2, or stop here." Files: `MessageList.tsx`, `turn.py` notice payload.
8. **Cheap by default when an LLM is needed** (S). Preselect the lowest-priced capable model for follow-ups, show EUR per build in the picker, and keep delegation to a Haiku-class worker visible (C9). Files: `ModelPicker.tsx`, `ChatSettings.tsx`, `llm_config.py`.

Dependencies: 1 needs a match/estimate source before it can say "free"; 2, 3, 4 and 6 build on 1's verdict and can start in parallel with UI-only stubs; 5 and 7 are independent of 1; 8 is independent.

---

## Top 5 problems

1. No cost or time estimate before Send; real data shows a 31-part ship cost EUR 1.50 of the 2 EUR budget (C1).
2. The budget caps the whole chat, so follow-ups after the first model have little or nothing left (C2).
3. Budget reached is a grey line under a green "Done"; the fix is hidden in a small chip and a select (C3).
4. No past costs per chat or in total; unpriced chats read as "EUR -", the EUR rate is not editable in the UI (C6, C7).
5. My Models has no versions, sort, or chat grouping; file names are the title and the "back to chat" link is tiny (F1-F4).

## Top 5 fixes

1. Pre-send "path and cost" line (free path vs AI build with estimated EUR and minutes), plus "Non-LLM paths" items 1-3 (M).
2. Actionable budget-reached banner with "Continue with +EUR 2", red chip and "Stopped" header (S-M).
3. Budget per request (M).
4. Cost per chat in the sidebar, a total, and cost per model on cards (M).
5. Version grouping with sort/filter and human titles plus "Continue in chat" on My Models (M, in one stream with S fixes 6 and 7).
