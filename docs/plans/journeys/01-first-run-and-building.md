# Journeys 1 and 2: first run, and idea to model and iteration

Status: audit only, nothing changed in the product. Ranked friction list for the owner to choose fixes from.

## Method and limits

- Real app built from this branch (`ldraw-nova-audit-1`, port 8801, own volumes), driven with Playwright (Chrome, swiftshader) at 1440 px and 390 px. Every screenshot was read.
- Journey 1: started from an EMPTY instance. No real key. The one network call was the Settings "Test" button with a fake key (free list-models call, the provider rejected it). No chat was sent to any provider.
- Journey 2: the owner's three recorded chats copied read-only into the instance (starship, cottage, red car). No live run and no iteration turn could be performed, so the iteration findings come from the recorded red-car chat (7 user turns, 2 published models) plus the code (`tools.py publish_model`, `prompts/system.md`, `ChatPage.tsx`, `BuildHeader.tsx`, `ModelCard.tsx`, `chatModels.ts`). Nothing was mocked with `page.route()`; the live-run visuals (build header while running, worker groups, render strip while streaming) were already audited in `docs/plans/chat-redesign/PROPOSAL.md` and the current components (`BuildHeader`, `StepGroup`, `WorkerGroup`) implement them, so I did not re-audit those.
- Not repeated from PROPOSAL.md: those items are fixed (composer toolbar, build header, step groups, Settings at the bottom, collapsible sidebar, single-line history).
- Screenshots (8, in `screenshots/`): j1-01 empty home, j1-02 key test fail, j1-03 models and roles, j2-01 long transcript, j2-02 and j2-03 the two red-car cards, j2-04 phone, j2-05 phone done header.

---

## Journey 1: first run

### Steps as the user experiences them

1. Opens the app. Sees "What should we build?", four example prompts, a composer with a yellow notice "No model configured yet. Add a model in Settings to start building." (j1-01). Model chip says "Choose a model" (disabled), Send is greyed.
2. Types a prompt. Send stays greyed. Nothing says why except the notice above; clicking Send does nothing.
3. Settings is the last item in the sidebar, bottom left; findable. On a phone it is inside the drawer behind the hamburger (works, but see F-8).
4. Settings page: Providers (4 key cards), Models (4 more sections), Roles, collapsed Advanced. Pastes the Anthropic key, clicks Save key. Badge turns "Key set" and a line says "Key saved. Click Test to check it." Clicks Test. With a bad key: "The provider rejected this key." (j1-02). The badge still says "Key set".
5. Scrolls past the other three provider cards (about 640 px) to Models. Anthropic now lists Opus 5.5, Sonnet 5.5, Haiku 5.5, Fable 5.1 with prices (j1-03). Clicks Add on one or more.
6. Roles: "Main model" already shows the first model added; "Worker model" shows "No workers". Nothing was chosen by the user.
7. New chat. Composer toolbar now shows the model, "Budget EUR 2" and the settings button; Send is enabled once text exists. The first build starts.

Effort to the first build with a valid key: about 8 actions (open Settings, paste, Save, Test, Add, New chat, type, Send), under two minutes after the user already has a key. The real cost is outside the app: creating a provider account and key. The app gives a "get one" link (good) but no hint which provider is cheapest or easiest.

### Friction points

**F-1. The "Key set" badge and the Add buttons do not depend on the key being valid.** Saving a rejected key gives a green "Key set" badge (j1-02) and unlocks Models. A user can add models, reach the composer, send, and only then learn the key is wrong. Test is a separate, optional step that the user must notice. Evidence: `ProviderSettings.tsx` `save()` stores and reports success; the `Test` button only runs on click.
**F-2. A failing chat turn gives a generic provider error.** On any provider failure the saved chat message is "AuthenticationError: Provider request failed. Check login, model access and settings." (`backend/turn.py` line 76). It does not name the provider, does not say "key rejected", and does not link to Settings. The chat has by then consumed the user's first message and shows a half-started chat. The good error text exists only on the Settings Test button ("The provider rejected this key.").
**F-3. Nothing recommends a model.** The Models list is ordered as the catalogue (Opus 5.5 at $4/$20, Sonnet 5.5, Haiku, Fable at $10/$50 per 1M tokens) with no "recommended for building" marker and no cost per build. The first model added silently becomes the main model: in the test the user clicked Add on the first row (Opus, one of the dearest) and Roles then showed Opus as the main model without any message (j1-03). A first-time user has no way to guess a build costs about 2 EUR.
**F-4. Page order hides the next step.** Providers (4 cards, about 640 px tall at 1440 px, 1,000+ px on a phone) come first, then four Models sections of which three say "Add your X key above to choose models" (j1-03). The user has to scroll twice to see Models and Roles; nothing carries them there after Save/Test succeeds. Three of the four providers are noise for a user who has one key.
**F-5. Empty state is a dead end for the keyboard-first path.** The composer is fully disabled with a notice, the example prompts still fill the box, but Send stays disabled with no tooltip (`title` is empty). The notice link is the only way out and it is the same red as the page's other text links, small, inside a yellow bar (j1-01).
**F-6. Home copy does not say what a first build costs, takes or needs.** "Turn your ideas into LEGO models you can explore in 3D. Start building with an agent, or use Plan mode..." Nothing about: it uses your own provider key, roughly how long (the recorded chats take 1.5 to 4 min for small models and much more for big ones), roughly what it costs (EUR 1.50 for the starship). Plan mode is mentioned but lives in the chat settings popover, so the first-run user cannot find it from this page.
**F-7. Worker model is jargon on first run.** "Worker model (cheap jobs, optional)" and "Needs Full access in the chat" (Settings, Roles) mean nothing before the first build. It is optional, correctly defaulting to "No workers", but the section is placed as step 3 of the page and reads as required.
**F-8. First visit on a phone.** The hamburger is a bare glyph (aria-label exists); Settings is two taps deep. The notice and composer work, and there is no horizontal scroll (scrollWidth 390). Minor.
**F-9. A fresh instance has no "first build" help.** History says "No chats yet." My Models and My Parts are empty without explanation (My Parts needs a Rebrickable CSV and the default parts mode is "off" without it, which is fine, but a new user does not learn what My Parts is for).

### Journey 1 fixes (ranked)

| # | Fix | Impact | Effort | Files |
|---|---|---|---|---|
| 1 | Verify the key on Save (run the free Test automatically) and show "Key works / rejected" in the badge; keep Add disabled until it works or the user overrides. | High | S | `components/ProviderSettings.tsx` |
| 2 | Name the provider and cause in failed-turn errors: map 401/403 to "Anthropic rejected your key. Open Settings" with a link; keep the generic text for other failures. | High | S | `backend/turn.py`, `components/MessageList.tsx` (banner with link) |
| 3 | Mark one model per provider "Recommended for building" and pre-select a mid-price model as main; show the first-build budget hint ("A small model costs about EUR 1 to 2"). | High | S | `components/ProviderSettings.tsx`, `backend/model_catalog_data.json` (flag), `pages/Settings.tsx` |
| 4 | Guided first-run strip on Settings: one card "1 Paste key, 2 Test, 3 Pick a model, 4 Build" that collapses providers without a key behind "Other providers" and scrolls to Models when the key tests ok; "Start building" button at the end. | High | M | `pages/Settings.tsx`, `components/ProviderSettings.tsx`, `styles/settings.css` |
| 5 | Empty-state on Home: when no model, replace the composer notice with a primary "Add your API key" button and a short "what it costs / how long" line; add the tooltip to disabled Send. | Medium | S | `pages/Home.tsx`, `components/Composer.tsx`, `styles/chat.css` |
| 6 | Move "Roles" up (Main model right after the models) and label the worker as "Advanced, optional". | Low | S | `pages/Settings.tsx` |
| 7 | Short "what is this" hint on My Models and My Parts empty states. | Low | S | `pages/Models.tsx`, `pages/Parts.tsx` |

---

## Journey 2: idea to model, and iteration

### Steps as the user experiences them

1. Home: picks an example or types, Send. The chat opens; a build header shows "Step N/~M, phase, time, euros" with a Stop button (implemented in `BuildHeader.tsx`; not re-audited live).
2. While it runs: grouped steps ("Steps 1-11 LDraw Nova x4, Read file x3, ... 11 ok"), failed groups in red, render thumbnails, worker groups (j2-01 shows the grouped transcript of a finished run).
3. Finished: header collapses to "Done, 1 min, EUR -, 3 steps, [Jump to result]". The model card follows the last assistant message: render, `file.mpd, 48 parts`, a "1 warning" chip, description, "3D View", "3D Player", `.mpd`, `.glb`, `BOM` (j2-02). The chat reopens scrolled to the result. 3D View opens a good viewer modal (Normal/High/Poly, Inspect/Walk).
4. Iterate: the user types "make the roof blue" in the same chat (placeholder is still "Describe a model to build..."). The agent continues in its stored work folder (notes, plan files); a new model card appears at the end of that turn.

### What the recorded red-car chat shows about iteration (j2-02, j2-03)

Seven user turns, two cards: `red-speedster-v1.mpd, 48 parts` and `red-speedster-no-2x12-plate-v1.mpd, 50 parts`. Both have the same description ("Small red speedster; 4-wide, front -Z, ground Y=0") and both say "1 d ago". The user asked "I do not think to have a 2x12 red plaque" and got a model whose only visible difference is the longer file name and 2 more parts.

### Friction points

**G-1. Versions are not visible, and the naming can mislead.** The version is only the `-vN` suffix in the file name (`tools.py` `_next_version`), and it counts per file name slug the agent picks. When the agent changes the name, the counter restarts: the second red-car model is "v1" again (j2-03). The user sees two cards both "v1" with no order label, no "latest", no "replaces". `ModelCard.tsx` shows file, parts, warning, description, relative time and nothing else.
**G-2. No "what changed".** Nothing shows what differs between two revisions: no part-count delta (48 to 50 is visible only by reading both cards), no BOM diff, no before/after render, no changelog line. The description is copied unchanged. The assistant prose above the card is the only explanation, and on a 5,000 px transcript (red car 5,477 px at 1440 px, 6,597 px at 390 px) it is far from the card.
**G-3. It is not clear the agent continued from the existing model.** There is no visible link between the user's follow-up and the previous card. The prompt (`prompts/system.md`) tells the agent to resume from `output/NOTES.md` and the editor plan, not to "load the latest published model", so continuation relies on the agent's notes; the user is not told which model the new turn started from, and a failure to continue (starting over under a new name) is invisible, as in G-1. The composer placeholder stays "Describe a model to build..." and gives no hint that follow-ups edit the current model.
**G-4. The build header mixes scopes and under-reports.** For the red-car chat (7 turns) the header reads "Done, 1 min, EUR -, 3 steps", which is the last turn only (`usage.turn_steps`, `duration_s` are per turn) while the euro figure for the starship (EUR 1.50 / EUR 2.00) is for the whole chat. A user who iterates cannot tell what a follow-up cost versus the whole chat. "EUR -" is shown in big bold type with no explanation when a chat is unpriced (j2-02 header).
**G-5. Budget does not carry into a follow-up.** An existing chat opens its composer with the options saved on the chat. The red car has none, so the follow-up composer says "No budget" while the Home page defaults to "Budget EUR 2". Same user, same app, different default; the settings dot is on (j2-02) with nothing to say what is non-default.
**G-6. Model card actions do not cover iteration.** Download buttons and 3D View/Player are there, but "Edit this model" and "Ask for a change" are not on the card; "Edit in 3D" sits in the chat header (red text link, desktop only) and applies to the chat plan, not to a specific card. Two cards, one "Edit in 3D" link: which one it edits is unclear.
**G-7. Warnings.** Every card shows "1 warning", and the text is always the same ("Physical buildability is not proven; read the validation and visual review reports."). Expanding it (it works) tells the user nothing to do, and it appears on every model, so it reads as permanent noise and a real failure ("Toolkit validation failed. This revision is for inspection and needs repair.") would be buried as one more identical chip. The reports it refers to are links in the transcript prose, not on the card.
**G-8. A blank grey block in the phone transcript.** At 390 px with the chat opened at the top, the render thumbnail under "Steps 1-11" is an empty grey rectangle (j2-04), while at 1440 px the same image shows (j2-01). Likely `loading="lazy"` inside the scroller before the image enters view; the user sees a hole. Worth a check on a real device before fixing.
**G-9. Finished chats open on the last card, not on a summary.** Good for the single-model case (j2-05). With two or more cards (red car) it lands on the last card in the middle of the screen with no sign there is an earlier one except the sidebar thumbnails (two small images, no labels).
**G-10. My Models lists versions as separate unrelated entries.** My Models shows `red-speedster-no-2x12-plate-v1` and `red-speedster-v1` as two cards, not as one model with versions, with the same description.

### Journey 2 fixes (ranked)

| # | Fix | Impact | Effort | Files |
|---|---|---|---|---|
| 1 | Version label on the card: "Version 2 of 2 in this chat, latest", and an "earlier versions" chip; number versions per chat, not per file slug (fixes "v1" twice). | High | M | `backend/tools.py` (record version/previous id on publish), `backend/gallery.py` or chat model payload, `components/ModelCard.tsx`, `chatModels.ts`, `styles/model-card.css` |
| 2 | "What changed" line on every revision card: part-count delta ("+2 parts"), colour/part adds and removes from the two BOMs, and the one-sentence change from the agent. Computed deterministically from the two BOMs. | High | M | `backend/tools.py`, `backend/gallery.py`, `components/ModelCard.tsx` |
| 3 | Show what the turn started from: a "Continuing from red-speedster-v1" chip under the user's follow-up and the placeholder "Describe a change to this model, or a new model" once a model exists; tell the agent explicitly (system prompt line plus the latest model path) so continuation does not depend on notes. | High | S to M | `components/Composer.tsx`, `components/MessageList.tsx`, `backend/system_prompt.py`, `prompts/system.md` |
| 4 | Header scope: show "This turn" (steps, time) and "Chat total" (euros, turns) separately; replace "EUR -" with "cost unknown". | Medium | S | `components/BuildHeader.tsx`, `components/UsageBadge.tsx` |
| 5 | Carry the budget: new chats and existing chats start from the same default; show a persistent budget chip value. | Medium | S | `components/Composer.tsx`, `components/ChatSettings.tsx` |
| 6 | Card actions: "Change this model" (focuses the composer with the model referenced) and "Edit in 3D" on the card, not only in the chat header. | Medium | S | `components/ModelCard.tsx`, `pages/ChatPage.tsx`, `components/Composer.tsx` |
| 7 | Warnings: show only validation failures as a warning; move the standing "buildability not proven" text to a single quiet note; link the reports from the card. | Medium | S | `backend/tools.py`, `components/ModelCard.tsx` |
| 8 | Group versions in My Models (one entry, version switcher, compare). | Low | M | `pages/Models.tsx`, `components/ModelCard.tsx` |
| 9 | Check the phone render-strip hole (lazy images in the scroller). | Low | S | `components/StepGroup.tsx`, `styles/chat.css` |

---

## Non-LLM paths (owner direction: favour deterministic, LLM at the margin)

Only what the user would experience. The pipeline design is another agent's job. Evidence that the engine exists: the toolkit already has deterministic starting points the agent calls today (`ldraw-agent vehicle list/plan <name> --palette`, `design palettes`, `spaceship brief`, architecture and boards modules, `build` from a JSON plan, the manual editor with undo/redo), and the red-car transcript opens with 11 steps of tool calls, reads and listings (j2-01) before anything is built.

Ranked by user value and effort:

1. **Start from a model type form instead of a blank prompt (High, M).** Home gets "Start from a type": Car, Cottage, Starship, Crane, Tower; each shows 3 to 5 controls (size, wheels or roof style, main colour from palettes) and a live preview render. "Build" runs the deterministic generator and publishes a card in seconds with EUR 0.00. The prompt box stays as "Describe something else". Files: `pages/Home.tsx`, new `components/TypeForm.tsx`, backend endpoint that calls the toolkit's plan/build, `ModelCard` shows "Made from preset: Car, v2".
2. **Colour and part-swap follow-ups as direct operations (High, M).** The card (or a "Quick change" bar above the composer) offers "Change colour of: roof / body / wheels" with a colour picker, "Bigger wheels", "Longer", applied to the plan JSON and rebuilt without a model turn; each produces a new version with the "what changed" line (needs Journey 2 fixes 1 and 2). "Make the roof blue" typed in the composer can be recognised by a cheap rule or small model and offered as "Apply as a quick change (free)" with "Ask the agent instead" as the fallback. Files: `components/ModelCard.tsx`, `components/Composer.tsx`, backend plan editing, `components/editor/*`.
3. **Presets for the budget and effort (Medium, S).** "Quick (free, preset)", "Standard", "Detailed (agent)" as one choice replaces choosing a model for people who just want a car. Files: `components/Composer.tsx`, `components/ChatSettings.tsx`.
4. **Use the editor for edits that are clearly manual (Medium, S).** Offer "Edit in 3D" prominently on every card after a build so the user can recolour and move parts directly (the editor already supports this and is free). Files: `components/ModelCard.tsx`, `pages/ChatPage.tsx`.
5. **No-key mode (High for first run, M).** If presets work with no provider key, a first-time user gets a first model in the first minute, before any Settings step; the LLM then becomes an upgrade ("Describe something custom: add a key"). This removes F-1 to F-4 from the critical path. Files: `pages/Home.tsx`, `components/Composer.tsx`, backend.

## What can be built in parallel

Independent (separate files, no shared logic):
- J1 fixes 1, 3 and 6, 7 (Settings and ProviderSettings only), except 3 touches the catalogue JSON.
- J1 fix 2 (backend `turn.py` plus one banner) and J1 fix 5 (Home and Composer notice).
- J2 fix 4 (BuildHeader and UsageBadge) and J2 fix 9 (StepGroup/CSS).
- J2 fix 7 (warnings, `tools.py` and `ModelCard`).

Coupled (do together or in order):
- J2 fix 1 then fix 2 then fix 8: all need a per-chat version record from `publish_model`; do 1 first (payload), then 2 and 8 on top.
- J2 fix 3 and fix 6 both edit `Composer.tsx`; Non-LLM paths 2 and 3 do too. Do them as one change.
- J2 fix 5 and fix 3 share `Composer.tsx`/`ChatSettings.tsx` options defaults.
- Non-LLM paths 1, 2 and 5 depend on the deterministic pipeline design and should wait for it; paths 3 and 4 do not.

## Top 5 problems

1. A rejected key still shows "Key set" and unlocks models; the user only learns at the first failed chat (F-1, F-2).
2. Versions are invisible and mislabelled: the red car has two cards both "v1", same description (G-1, G-2).
3. It is never shown that a follow-up continues from an existing model, or what changed (G-2, G-3).
4. First-run Settings is a long page with no guided path, no recommended model, and the first-added model silently becomes the main one (F-3, F-4).
5. The header mixes per-turn steps and time with per-chat euros, and shows "EUR -" unexplained (G-4).

## Top 5 fixes

1. Validate the key on Save and show the real provider error in chat (J1 fixes 1 and 2, S).
2. Per-chat version number and "what changed" on the model card (J2 fixes 1 and 2, M).
3. "Continuing from vN" chip, changed placeholder and explicit latest-model path for the agent (J2 fix 3, S to M).
4. Guided first-run strip plus a recommended default model (J1 fixes 3 and 4, S to M).
5. Quick changes and a type-form start that skip the LLM for colour and size edits (Non-LLM 1 and 2, M, after the pipeline design).
