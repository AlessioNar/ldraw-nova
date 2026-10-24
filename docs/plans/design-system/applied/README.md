# Design system applied: Blueprint with a hint of Toy box

Screenshots of the real app (1280x800 unless noted), dark and light: `home-*`, `settings-*`, `models-*`, `parts-*`, `chat-*` (starship chat, wide layout with the 3D stage), `chat-versions-*` (a chat with v1 and v2, "Changed since v1", spec strip), `chat-800-*` (800px wide: the old single-column behaviour), `editor-*` (the 3D editor page).

There is no theme toggle in the app: the theme follows the OS (`prefers-color-scheme`), dark is the default, and `<html data-theme="dark|light">` forces one. Light shots use Chrome's light preference, dark shots the default.

## What changed
- **Tokens** (`frontend/src/styles/theme.css`): Blueprint palette for both themes (slate dark, warm paper light), the old variable names kept, new ones added (`--surface-3`, `--border-strong`, `--fg-2`, `--accent-solid`, `--canvas`, `--font-*`, `--fs-*`, brick colours). `--accent` is for text and lines, `--accent-solid` for fills. WCAG AA values are the proposal's; accent text on its soft fill re-checked (5.2 dark, 4.6 light).
- **Fonts**: Hanken Grotesk and JetBrains Mono, self-hosted as latin variable woff2 in `frontend/public/fonts/` (served at `/fonts/`), with the two OFL 1.1 texts next to them. No npm dependency; system fallbacks. Mono is used for part counts, versions, costs, times, sizes, badges, download chips.
- **Blueprint**: measurement grid behind every page, 1px lines, outlined New chat button, red rule on the active nav item and chat, History under a rule, user bubble with a hard border, dashed "Continuing from" chip, worker cards with a blue rule, budget stop with a 3px danger edge, model card with a spec strip (Parts, File size).
- **Toy box hints**: radii 4/7/11 px (Blueprint is 3/5/8, Toy box 8/14/22); the primary button has a 2px red edge that presses in on `:active` (only `button.primary`); four-brick-colour row on the home head (replaces "Sheet 01"); the four suggestion prompts get brick-colour square markers; the latest model card of a chat has a thin four-colour top edge that draws itself once (not under `prefers-reduced-motion`).
- **Viewer pages** (`app/web/viewer`): `tokens.css` is a copy of the token block (the viewer pages cannot import the app CSS), used by `viewer-common.css`; bars, buttons, segmented controls, status chips, player slider follow the theme.
- **Chat page, wide screens (>= 1100px)**: conversation column (40%, 380-560px) on the left, one embedded 3D viewer stage on the right. It shows the latest model; "3D View" / "3D Player" on any model card (and the sidebar thumbnails and viewer links in messages) show that version on the stage. The stage header has the version, the 3D View / 3D Player switch, Open editor and Open in new tab. The stage component is lazy-loaded (own chunk, only fetched on wide chat pages) and there is a single iframe. Below 1100px nothing changes: cards in the conversation, 3D opens in the modal.
- **No model yet**: the stage stays and shows a quiet empty state (grid, brick row, one sentence). Chosen over a full-width chat because the layout does not jump when the first model arrives, and the user sees where it will appear.

## Deviations from the proposal
- Dropped as asked: "Sheet 01", "PROMPT A-D" labels, dimension-line decorations. Small-caps labels are 11px (proposal 10.5px). No corner ticks or crosshair marks on the model card.
- Spec strip has Parts and File size only: colours count and dimensions (x studs) are not in the client data (needs a backend change). Same for the colour swatches per chat in the sidebar: skipped.
- No agent avatars in the transcript (needs TSX); the agent text is unchanged.
- The 3D scene backdrop is still the light grey set in `glb-view.js` / `player.js` (Poly mode assumes a light background), so the viewer is a light panel inside the dark theme. Making it theme-aware is a small JS change left for later.
- The embedded viewer page keeps its own top bar (file name, rendering and camera modes), so the stage shows two thin bars; an `?embed=1` mode would remove the duplicate title.
- My Parts: CSS only (mono for numeric columns); TSX untouched.
- The editor palette/inspector mock from the proposal was not built (new work, out of scope).
