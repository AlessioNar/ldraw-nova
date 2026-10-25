# Chat interface redesign: audit and proposal (phase 1)

Status: proposal, for owner approval. Nothing here is implemented.

Method: the real app (frontend built from this branch) in an isolated container, driven with Playwright at 1440 px and 390 px. Content: the owner's three recorded chats (cottage: 92 messages, 49 tool cards, 7,088 px of scroll on desktop and 8,043 px on phone; red car; starship: 12 tool cards, interrupted at 0.72 of 2.00 EUR). Two things were mocked with `page.route()`: the model list (so effort, context and the workers picker show, as they do for a configured user) and a live run with three workers (the recorded chats contain no delegate calls). Screenshots are in `screenshots/`; the "Reconnecting to live updates" banner in 06 and 07 is an artefact of the mocked event stream, not a bug.

Screenshots: 01 new chat desktop, 02 new chat phone, 03 long build desktop, 04 model card desktop, 05 model card phone, 06 live run with workers desktop, 07 live run phone.

Owner requirements treated as fixed: (1) far fewer controls on the chat; (2) Settings at the bottom of the sidebar; (3) collapsible sidebar, state remembered.

## 1. What the user sees today

### 1.1 Starting a chat (`pages/Home.tsx`, `components/Composer.tsx`) - screenshots 01, 02
- Hero text, four example buttons, then the composer. The composer toolbar has up to 8 controls plus Send: model, mode, permissions, parts (a wide button, "Parts: Only . pileometer-to-rebrickable..."), effort, context, worker model (with a price string next to it), budget. On desktop they wrap onto two rows; on a phone they take 4 rows (152 px of 844 px).
- The three that matter at start are model, budget and (for this owner) parts. The rest are expert knobs that default sensibly: permissions, effort, context size (1M, never changed), mode.
- Effort and context only appear for models that have a profile, so the toolbar changes shape when the model changes. The controls jump around.
- "Upload a parts list under My Parts" renders as a toolbar button when no inventory exists, which is a navigation link dressed as a control.
- The worker picker shows "$3 in . $15 out per 1M tokens" inline: noise before the first message, but exactly the kind of cost information the owner wants, so it should live in the settings popover next to the budget.
- Budget defaults to "No budget". The owner's target is about 2 EUR per medium model; the default should be a budget, not none.

### 1.2 A long build streaming (`MessageList.tsx`, `ToolCard.tsx`, `TurnStatus.tsx`) - screenshots 03, 06
- Every tool call is a full-width bordered row, in a flat vertical list, interleaved with a one-line assistant sentence ("I'll check which way the headlight brick faces...") and a grey usage line. A 20-step build is 49 cards and 22 prose snippets; the cottage chat is 7,088 px long. Nothing says "step 14 of ~25" or what phase the build is in; the user has to scroll and count.
- Usage line after every assistant message (`MessageList.tsx`, `m._usage` -> `UsageBadge`): 38 lines like "60.94K in . 18.79K out . 59.18K cached . 761 cache write". These are token counters nobody can act on; they appear 38 times and separate every step. They hide the one number that matters (euros) and show it only in the header.
- `report_progress` renders twice: as a tool row ("Progress") and as a plain paragraph under it (`ToolCard.tsx`, `progress-summary`). In 03 the paragraph is a raw markdown string ("Plan: ... - **Palette:** ...") shown as unformatted text. It is the best human-readable summary in the whole transcript and it is formatted the worst.
- Status dots (8 px green/red/orange) are the only state signal. In the starship chat the last row is red "interrupted" and the user cannot tell whether that is a failure, a stop or an old run.
- Failed checks look like finished work at a glance: tool rows that return `Error:` get a red dot, but there is no grouping, so a retry loop (the same command 4 times) is 4 rows.
- Running tool output (`live-tool-output`, last 4,000 chars) is an unframed `pre` block below the row (screenshot 06: "Placing 41 of 118 parts..."), visually detached from its card, and it grows the page while streaming, which makes the auto-scroll jump.
- `TurnStatus.tsx` has a good idea (a status line with the elapsed time, `10:17`) but it is a full-width bar that sits above the composer, outside the transcript column, and shows only a phase string ("Running LDraw Nova") or "N workers running". It carries no step count, no cost and no budget bar.

### 1.3 Worker cards (`WorkerCard.tsx`) - screenshots 06, 07
- Each delegated worker is two stacked things: a collapsed `<details>` row (role, task text cut to one line) and a separate meter row beneath it (status chip, model name, `9 / 20 steps`, `EUR 0.11 of EUR 0.30`, progress bar). Three workers are 6 rows, with the meter visually floating between cards.
- Workers run in parallel but are listed in sequence as ordinary tool rows, in the middle of the planner's calls, so they do not read as a group ("3 workers, 2 running, EUR 0.38 of EUR 0.80").
- `stopped: stuck` and failure notices are only visible in the chip; the explanation lines are only in the result inside the collapsed details, except the `worker-notice` rows, which show only after the result arrives.
- On a phone the meter wraps to two lines per worker (screenshot 07) and the task text is cut at about 15 characters.

### 1.4 Renders and model cards (`ModelCard.tsx`, `ToolCard.tsx`, `placeChatModels`) - screenshots 04, 05
- The model card (render, name, parts count, a "1 warning" chip, description, 3D View, 3D Player, .mpd, .glb, BOM) is placed after the message that published it. It is good, but it is a single 420 px card at the end of 7,000 px of transcript; the finished result is the thing the owner waited 20 minutes for and it is not pinned anywhere. Re-opening the chat lands on the end, not on the result, unless the URL hash is `#model-...`.
- Intermediate renders (`view_image`, `extraImages`) are 360 px images in the flow; five in the cottage chat, each pushing the transcript down by about 250 px. They are the useful progress evidence, but they are scattered.
- The phone model card is fine (screenshot 05); the downloads wrap onto one row.

### 1.5 Cost and time
- Cost lives only in the chat header, as a single run-on string: `EUR 0.72 of EUR 2.00 . 313.49K in . 56.3K out . 260.11K cached . 45.92K cache write . 8 steps` (`UsageBadge.tsx`). The euro figure is bold but the same size as the token counters; on a phone it wraps to three lines and, in the 390 px header, the chat title disappears completely (only "Edit in 3D" and the usage string are left; see 05 and 07). A user on a phone cannot see which chat they are in.
- The planner/worker split exists only as a tooltip (`title` attribute) on the euro figure. There is no "where the money goes" view: per step, per worker, or planner vs workers.
- The budget stop is silent until it happens: the figure turns red at 80 % (`near-budget`), nothing else changes.
- Elapsed time exists only in `TurnStatus`, and only while running; finished chats show no duration at all.

### 1.6 Sidebar (`Sidebar.tsx`, `styles/layout.css`) - screenshots 01, 03
- 288 px wide, always visible on desktop; on a phone it is a drawer opened with a bare hamburger glyph.
- Top block: brand, "+ New chat", then "My Models / My Parts / Settings", then history. Settings sits with the content pages; it belongs at the bottom, away from the daily actions.
- History cards are 40 to 100 px each, with alternating backgrounds, titles cut at about 24 characters ("Please design a small white and blue") and up to four 46x34 thumbnails. Few chats fit; titles are unreadable. The active card in this theme is a pale red that also looks like an error state.
- A running chat is a tiny pulsing dot only.
- No way to hide it. At 1440 px the chat column is 820 px in a 1152 px area, so the sidebar costs real reading width on laptops.

### 1.7 Phone width, summary
- Composer: 285 to 350 px of 844 px permanently (toolbar 152 px + input + hint). During a build, status bar + composer take about 40 percent of the screen (07).
- Header: title lost (see 1.5).
- Worker meters and tool summaries are truncated to a few characters.
- Everything works, but the transcript is a tiny window over a large chrome.

## 2. Top problems, ranked

1. The composer toolbar has 8 controls, most of which nobody changes per message (permissions, mode, effort, context, worker model). It wraps to 2 rows desktop, 4 rows phone, and changes shape per model.
2. A build is an undifferentiated list of 49 equal-weight rows. No progress, no phases, no grouping; hard to find the result or the failure.
3. Cost is hidden in a header string that mixes euros and token counters, wraps on phone and hides the title; the 38 per-step token lines add noise but not the one thing the owner cares about (where the euros go, planner vs workers).
4. Worker cards are not grouped and cost two rows each; the swarm reads as noise instead of "3 workers, 2 running".
5. The sidebar cannot collapse, has Settings in the top block, and its history cards are tall with truncated titles.
Honourable mentions: `report_progress` text duplicated and unformatted; no time elapsed after the run; live tool output detached from its card; the finished model is not surfaced.

## 3. Proposal

### 3.1 Principles
- One screen answers "what is it doing, how far, how long, how much" without scrolling: a **build header**.
- The transcript is for conversation and results. Tool calls are evidence: collapsed into step groups, one line each, expandable.
- Every message-level control is something the user changes per message. Everything else is a per-chat default in one popover.

### 3.2 Composer toolbar: what stays visible and why

Visible, always (at most 4 items):
1. **Model** (the planner model chip). Per message: the most common reason to switch, and the thing that drives price.
2. **Budget chip** showing the euro figure ("Budget EUR 2"), clicking opens the same popover focused on budget. Owner's primary constraint; one glance before sending. Default: EUR 2 instead of "No budget".
3. **Settings button** (sliders icon). Opens the popover (below). A small dot when a non-default setting is active (for example Plan mode, workers on, Read only).
4. **Send / Stop**.

Left of the textarea: one paperclip menu (Attach image / Attach document) instead of two icons.

Moved into the settings popover (per-chat defaults, saved to `chat.options` as today, no API change):
- Mode (Agent/Plan) as a segmented control at the top: it changes behaviour of the whole chat.
- Permissions (Ask / Full / Read only), with a one-line explanation under the selected value; hidden in Plan mode as today.
- Parts steering: "Only my parts / Prefer / Off" plus inventory picker (the current `PartsControl`); keep the full panel, just not in the toolbar. The summary line "Parts: Only, 1 list" appears as the popover row subtitle.
- Reasoning effort and context window, grouped under "Advanced" (collapsed by default); hidden when the model has no profile entries, so the toolbar no longer changes shape.
- Workers: model picker plus its price, with the caps (steps, EUR) shown as read-only text.
- Budget: the number field/choices (1, 2, 5, 10, 20, custom); the toolbar chip is a shortcut to this row.

Why this split: model, budget, send are decisions before every message and need to be visible for cost control. Everything else is a stable preference of a chat, and the new chat page can start from the last-used values (already done by `loadSteering` for parts; extend to the rest), so it never needs to be re-chosen. Net: 8 controls become 3 chips plus Send; phone toolbar goes from 152 px to one 44 px row.

Other composer changes: hide the Enter hint on touch/phone and on focus-less state (already hidden at 820 px; keep); the "No model configured" notice stays above the input but becomes one line with a link; on a phone the composer collapses to a single row (attach, input growing to 3 lines, send) while a build runs (see wireframe).

### 3.3 Build header (new; replaces `TurnStatus`'s bar and the `UsageBadge` in the header)

A sticky strip at the top of the chat, under the title, always visible while scrolling:

```
Starship build   Step 14 / ~25  .  Phase: Assembling    10:17     EUR 0.72 / 2.00  [=======---]   [Stop]
```
- **Progress**: step count from tool calls so far over an estimate (the median run length per mode, 25 for agent builds, shown as "~25" and never above 100 percent), plus the latest `report_progress` summary as the phase text (it already exists and is the best human text). Also the worker count: "2 workers running".
- **Time**: elapsed while running (already computed in `TurnStatus`); total duration afterwards ("Took 18 min").
- **Cost**: euros against the budget as one bar, turning amber at 80 percent and red at 100. Click to expand a small panel: planner EUR, workers EUR (the split `UsageBadge` already computes), per-worker rows, and the token counters (in/out/cached) as secondary text. This is where the "where does the money go" lives.
- Title stays on its own line (also on phones; the "Edit in 3D" link and the token string no longer fight it).

Finished state: the header collapses to a one-line summary: "Done . 18 min . EUR 1.74 . 25 steps . 1 model" with a "Jump to result" button that scrolls to the model card.

Backend: no new endpoints needed. Elapsed, steps, workers and euros are already in the stream (`activity`, `worker_*`, `usage`). Total duration for finished chats needs `activity.started_at` or first/last message timestamps (messages have ids and `updated_at`; verify, else add a `duration_s` to the chat payload).

### 3.4 Transcript: how tool calls and workers are grouped

- **Step groups.** All consecutive tool calls between two assistant prose messages become one group row: "Steps 11-13 . Ran shell, Wrote file, LDraw Nova . 3 ok". Collapsed by default; expanding shows today's `ToolCard` rows unchanged. A group containing a failure is expanded and tinted, with the failed call first. A running group is expanded, showing only the running call and its live output inside the card.
- **Retries** (same tool, same summary, consecutive) are merged to "LDraw Nova build x4, last ok".
- **Prose** (the model's one-sentence narration) stays as is, at normal weight; it is the story. `report_progress` is rendered once, as a formatted markdown callout (Markdown component), not as a row plus a plain paragraph.
- **Usage lines** per message are removed from the flow. Token counters move to the cost popover and to a "details" toggle per group (tooltip level), not 38 lines.
- **Live tool output** moves inside its running card, capped at 8 lines with its own scroll, so the page height stops jumping.
- **Interrupted/failed** gets a labelled chip ("Stopped by you", "Budget reached", "Error") on the group; a red dot alone is no longer used.
- **Workers**: one **Workers group** per parallel delegate batch, one header line ("Workers: 3 . 2 running, 1 stuck . EUR 0.38 of 0.80") and one compact line per worker: role, task (2-line clamp on phone), `9/20 steps`, `EUR 0.11/0.30`, thin progress bar, status chip, all in the same row (meter folded into the row; no second row). Expand a worker for task, transcript and output files as today. `stuck`/`failed` notices render directly under the worker line, not in the details.

### 3.5 Renders and model cards
- Intermediate renders (`view_image`/`render_model`) become a thumbnail strip (about 96 px high, scrolls horizontally) at the end of their step group, instead of 360 px full-width images; click opens the existing viewer modal/lightbox. Five images cost 100 px instead of 1,250 px.
- The **final model card** is pinned: it stays where it was published, but the build header's "Jump to result" scrolls to it, a finished chat opens scrolled to it (reuse the `#model-<id>` anchor code in `ChatPage.tsx`), and the sidebar thumbnail already deep-links to it. The card itself keeps its layout; the warning chip becomes a clickable "1 warning" that expands the list, and "Edit in 3D" moves to the card's action row (currently in the chat header; coordinate with the agents editing the link in `ChatPage.tsx` right now).
- On phones the card is full width, buttons wrap in two rows; no change in structure.

### 3.6 Sidebar: Settings at the bottom, collapsible

- Order: brand + collapse button, "+ New chat", My Models, My Parts, History (scrolls), then a footer pinned to the bottom: **Settings** (with its icon) and nothing else. The footer is outside the scrolling list so it stays reachable with a long history.
- History cards become single-line rows (title ellipsised to the full width, relative time on the right, a pulsing dot for running chats) with thumbnails only on the active chat or on hover; active item uses the neutral accent-soft background, not red-ish; a trash icon on hover (always on touch). This fits about 12 chats instead of 5.
- **Collapse**: a chevron button in the brand row.
  - Desktop expanded: 288 px. Desktop collapsed: a **48 px icon rail** (brand mark, "+" new chat, models, parts icons, a flat list of dots for running chats is optional and not v1, settings icon at the bottom, the expand chevron at the top). Icons get `title` and `aria-label`. History is hidden in the rail; the rail's expand button (or "+") is how you get back.
  - State remembered per viewer in `localStorage` (key `ldraw-nova.sidebar`, values `expanded|collapsed`), every read and write inside try/catch, default expanded; the app must render correctly when storage throws.
  - Phone (<= 820 px): no rail. The sidebar is a full-height drawer, closed by default and always closed after navigation (as today); the stored desktop state is ignored at that width. The hamburger gets an `aria-label`, and the drawer gets a close button in its brand row. Keyboard: `Esc` closes the drawer; a shortcut (`Ctrl+B`) toggles collapse on desktop.
  - Implementation: a class on `.app` (`sidebar-collapsed`) switches `grid-template-columns` from `var(--sidebar-w)` to `var(--rail-w)` (48 px) with a 0.15 s transition; the sidebar hides labels and the history block via CSS, so the DOM stays the same.

## 4. Wireframes

### 4.1 Desktop, sidebar expanded, build running (1440 px)

```
+---------------------+--------------------------------------------------------------+
| [=] LDraw Nova  [<] | Please design a small white and blue starship                |
| [ + New chat      ] +--------------------------------------------------------------+
| My Models           | Step 14/~25 . Assembling hull   10:17   EUR 0.72 / 2.00 [=== ]|
| My Parts            |  Workers 3 (2 running)   [cost v]                  [ Stop ]  |
|                     +--------------------------------------------------------------+
| HISTORY             |  (you) Please design a small white and blue starship        |
| * Please design...2m|                                                              |
|   A 6x8 cottage 21h |  Plan: gable-front, 6 palette colours ...  (formatted)      |
|   Red car       23h |  > Steps 1-6   Searched parts, Ran shell x4      6 ok        |
|                     |  > Steps 7-9   Wrote file, Ran Python, Build     3 ok        |
|                     |  I'll build the plan and run the inventory check.            |
|                     |  v Steps 10-13 (running)                                     |
|                     |     * LDraw Nova build output/azure-dart.mpd      51s        |
|                     |       Placing 41 of 118 parts...   (live, 8 lines)           |
|                     |  Workers: 3 . 2 running, 1 stuck . EUR 0.38 of 0.80     [v]  |
|                     |   o Builder  left wing ...  9/20  EUR0.11/0.30 [==  ] running|
|                     |   o Builder  right wing ... 6/20  EUR0.07/0.30 [=   ] running|
|                     |   ! Reviewer nacelle ...   12/12  EUR0.20/0.20  stopped: stuck|
|                     |       Stopped as stuck after 12 steps (runner message)       |
|                     |  [thumb][thumb][thumb]  <- renders so far                    |
|                     |                                                              |
| ------------------  |  +--------------------------------------------------------+  |
| (gear) Settings     |  | [clip] Describe a model to build...                    |  |
+---------------------+  | [claude-sonnet-4-5 v] [Budget EUR 2] [sliders .]  [Stop]|  |
                        |  +--------------------------------------------------------+  |
```

Finished: header becomes `Done . 18 min . EUR 1.74 . 25 steps . [Jump to result]`; all groups collapsed; the transcript is prose plus the model card.

### 4.2 Desktop, sidebar collapsed (48 px rail)

```
+----+------------------------------------------------------------------------------+
| [>]| Please design a small white and blue starship                                |
| [+]| Step 14/~25 . Assembling hull   10:17    EUR 0.72 / 2.00 [====   ]   [ Stop ] |
| [M]|                                                                               |
| [P]|     (transcript column gets about 290 px wider room; max-width stays 820)     |
|    |                                                                               |
|    |                                                                               |
|[gear]| [composer]                                                                 |
+----+------------------------------------------------------------------------------+
```
(M = My Models, P = My Parts icons, with tooltips; gear = Settings pinned at the bottom.)

### 4.3 Phone (390 px), sidebar closed (default) and open

```
Closed, build running                         Drawer open
+--------------------------------+            +----------------------+---------+
| [=]  Please design a small w...|            | LDraw Nova      [x]  | (scrim) |
| Step 14/~25 . 10:17            |            | [ + New chat ]       |         |
| EUR 0.72 / 2.00 [=====     ]   |            | My Models            |         |
+--------------------------------+            | My Parts             |         |
| (you) Please design ...        |            | HISTORY              |         |
| > Steps 1-9  (9 ok)            |            |  * Please design.. 2m|         |
| I'll build the plan and run... |            |    A 6x8 cottage 21h |         |
| v Steps 10-13 running          |            |    Red car       23h |         |
|   * build ...azure-dart  51s   |            | -------------------- |         |
| Workers: 3 . 2 running  [v]    |            | (gear) Settings      |         |
|  o Builder left wing   9/20    |            +----------------------+---------+
|    EUR0.11/0.30 [==   ]        |
|  ! Reviewer ...  stuck         |
| [thumb][thumb][thumb]          |
+--------------------------------+
| [clip] Describe a model...     |   <- one row while running, 3 lines max
| [Sonnet v][EUR 2 v][sliders][■]|   <- one toolbar row
+--------------------------------+
```
Header is two lines (title above; progress/cost row below, tap to expand the cost panel). Settings popover on a phone opens as a bottom sheet with the same rows.

### 4.4 Settings popover (desktop popover, phone bottom sheet)

```
+-----------------------------------------+
| This chat                               |
| Mode          [ Agent | Plan ]           |
| Permissions   [ Ask first v ]            |
| Parts         Only my parts, 1 list  >   |
| Workers       gemini-flash   $0.3/1M  v  |
| Budget        [ EUR 2 v ]                |
| > Advanced                              |
|     Reasoning effort [ Medium v ]        |
|     Context window   [ 1M v ]            |
| Defaults for new chats: [x] remember     |
+-----------------------------------------+
```

## 5. Implementation plan (after approval)

Common rules: no change to the stream/event or REST contracts except where stated; behaviour of existing tests stays; run `npm run build` and the frontend tests; add Playwright checks at 1440 and 390. Settings.tsx, EditorPage.tsx and the "Edit in 3D" link in ChatPage.tsx are being changed by other agents: base the work on the merged result and keep those edits.

### Task A: Sidebar (independent)
Files: `components/Sidebar.tsx`, `styles/layout.css`, `styles/responsive.css`, `styles/theme.css` (a `--rail-w`), `App.tsx` or the layout component that renders `.app` (class toggle), a small `hooks/usePersistentFlag.ts` (localStorage with try/catch).
Do: Settings footer pinned bottom; collapse button; 48 px rail at desktop; phone drawer with close button and Esc; single-line history rows; neutral active colour; aria labels.
Acceptance: Settings is the last item and visible with 50 chats; collapse state survives reload; with localStorage throwing the app still renders expanded; at 390 px there is no rail and the drawer closes after navigating; 1440 px collapsed gives the chat column at least 250 px more room; keyboard operable.
Parallel: yes, touches nothing else in the chat.

### Task B: Composer and settings popover (independent of A, C, D)
Files: `components/Composer.tsx`, new `components/ChatSettings.tsx` (popover/bottom sheet), `components/ModelPicker.tsx` (budget chip only if needed), `components/WorkerModelPicker.tsx`, `components/parts/PartsControl.tsx` (render inside the popover), `styles/chat.css`, `styles/parts.css`, `pages/Home.tsx` (default budget).
Do: toolbar reduced to model, budget chip, settings button, Send/Stop; paperclip menu merges the two attach buttons; popover with mode, permissions, parts, workers, budget, advanced (effort, context); remember last-used values for new chats; default budget EUR 2; dot on the settings button for non-default values; phone toolbar single row.
Acceptance: toolbar is one row at 390 px and 1440 px, with and without model profile; every option previously reachable is reachable in the popover and still sent in `TurnOptions` unchanged; Plan mode hides permissions as today; Escape closes; focus returns to the button.
Parallel: yes. Shares `chat.css` with C and D: keep selectors in separate blocks and split the file or rebase on merge.

### Task C: Build header with progress, time, cost (depends on nothing but is read by D)
Files: new `components/BuildHeader.tsx` (replaces the header usage in `pages/ChatPage.tsx` and the status bar part of `components/TurnStatus.tsx`; approvals and context notice stay in `TurnStatus`), `components/UsageBadge.tsx` (becomes the cost panel content with planner/worker split and tokens), `hooks/useChatStream.ts` (expose step count and totals; no event changes), `styles/chat.css`, optionally a `duration_s` field in the chat payload on the backend if timestamps are not enough.
Do: sticky header with title on its own line, step count (about 25), phase from latest `report_progress`, elapsed time, cost bar against budget with amber/red states, expandable cost panel, finished summary with "Jump to result".
Acceptance: header visible while scrolling at both widths; title never hidden on phones; euro figure is the largest number; finished chats show duration; cost panel matches the chat total shown in the sidebar/model list; no more than 2 header lines on a phone.
Parallel: yes with A and B; D can start together and merge after C only for `chat.css`.

### Task D: Transcript grouping, workers group and render strip (independent of A and B)
Files: `components/MessageList.tsx` (group consecutive tool calls; remove per-message `UsageBadge`), new `components/StepGroup.tsx`, `components/ToolCard.tsx` (live output inside the card, `report_progress` rendered via Markdown once), `components/WorkerCard.tsx` (single-row compact layout; notices under the row) and new `components/WorkerGroup.tsx` (batch header with totals), `components/ModelCard.tsx` (warning chip expandable, Edit in 3D in the action row if the other agent has landed), `styles/chat.css`, `styles/model-card.css`.
Do: step groups collapsed by default (failed/running expanded), retry merging, thumbnail strip with the existing lightbox, interrupted/stopped chips, workers group.
Acceptance: the cottage chat renders at under 2,000 px collapsed (from 7,088); a failed or stuck item is visible without clicking; running tool output stays inside its card and does not move the layout; live workers still map to their delegate calls (existing mapping in `MessageList.tsx`); existing tests for tool/worker cards updated; a finished chat opens scrolled to the model card.
Parallel: yes. It is the largest task; split in D1 (grouping, tool cards, render strip) and D2 (workers group) if wanted; D2 depends on the group component from D1 only for styling.

### Task E (optional, after A-D): phone polish and tests
Files: `styles/responsive.css`, Playwright checks under `app/web/viewer-deps/test/`.
Do: bottom-sheet settings at phone width, composer single row during a build, safe-area padding, a browser test per layout (expanded and collapsed sidebar, build header, workers, popover) at 1440 and 390 px.
Acceptance: no horizontal scroll at 390 px on new chat, a long chat, a live run with workers; tap targets at least 40 px.
Depends on A-D.

### Order
A, B, C, D run in parallel in separate worktrees (the only shared file is `styles/chat.css`; merge order B, C, D, then A or any, resolving CSS by section). E last.

## 6. Open questions for the owner
1. Default budget EUR 2 on new chats: yes or keep "No budget"?
2. Step estimate "about 25": use a fixed number, or the median of the owner's finished chats (cottage 38 steps per the header, starship interrupted at 8)? The proposal assumes a soft fixed estimate labelled with "~".
3. Rail or fully hidden when collapsed on desktop? Proposed: 48 px rail (keeps Settings and New chat one click away).
4. Should "remember last-used" settings apply globally (all new chats) or only to new chats from the home page? Proposed: global.
