# Design system proposal: Blueprint

**Idea.** Open Brick Designer is CAD for bricks, so it should look like a drafting table, not a chat app. A deep blue-slate base with a faint 24 px / 120 px measurement grid, thin 1 px lines, tiny corner ticks on the model card and "Sheet 01 / Configuration" dimension-line captions give it a technical feel. Everything that is a number (part codes like `3001`, counts, costs, versions, budgets) is set in a monospace, text stays in a friendly grotesque, and brick red is kept for signal only: the logo, the active item, version badges, the Send button, the budget stop. A light "paper print" theme (warm paper, blue ink) is the same system inverted.

Files: `tokens.css` (tokens, both themes), `components.css` (component sketches used by the mockups), `home.html`, `chat.html`, `settings.html`, `editor.html`, `screenshots/`. Open any page with `?theme=dark|light`; `home.html?rail=1` shows the collapsed sidebar. Without a param it follows the OS theme.

## Palette
| Token | Dark | Light |
|---|---|---|
| `--bg` | `#0b1424` slate blue | `#f2f0e9` paper |
| `--surface` / `--surface-2` / `--surface-3` | `#101b2f` / `#162439` / `#1d2e49` | `#fbfaf6` / `#ebe8df` / `#e0ddd2` |
| `--border` / `--border-strong` | `#26395a` / `#4a6791` | `#cdc9bb` / `#7d889e` |
| `--fg` / `--fg-2` / `--muted` | `#e8eef8` / `#b6c4dc` / `#8da0be` | `#15233b` / `#33456a` / `#5a6985` |
| `--accent` (text, lines) | `#ff5f52` | `#c42d23` |
| `--accent-solid` (fills) | `#d9382e` (the logo red) | `#c42d23` |
| ok / warn / danger / info | `#4fd9a0` `#f5b942` `#ff7a70` `#7fb4ff` | `#0b6e49` `#8f5a00` `#b0261c` `#1d4f9a` |

Dark red text had to be lightened (`#ff5f52`) because the pure logo red is only about 3.6:1 on the slate; fills keep `#d9382e`. The logo SVG is inlined with `currentColor`, so it takes `--accent`.

## Type
- **Text: Hanken Grotesk** (400/500/600/700). A clean, slightly geometric grotesque with open shapes that stay legible at 13 px, neutral enough to sit next to a monospace. Fallback: Helvetica Neue, Arial, system-ui.
- **Numbers and codes: JetBrains Mono** (400/500/600), slashed zero, tabular. Built for reading identifiers, so `3001` vs `3010` never blur. Fallback: ui-monospace, Cascadia Mono, Consolas.
- Both load from Google Fonts with `display=swap`; the stacks degrade to system fonts offline. The app is local-first, so consider self-hosting the two woff2 files (about 100 KB).
- Scale: 10.5 (small-caps labels, +0.08em tracking) / 12 / 13 / 14.5 body / 17 / 24 / 34. Spacing is a 4 px scale, radii 3/5/8 px (today 8-10 px), motion 100/160/280 ms.

## Components
- **Sidebar**: logo and name with a mono version line, outlined (not filled) New chat button with a red `+`, nav items with a 2 px red left rule when active, History under a small-caps rule, tiny mono timestamps, session cost in the footer next to Settings. The rail variant keeps icons only.
- **Composer**: hairline panel that turns red-bordered on focus. The "Continuing from **v2**: Red car" chip is a dashed red outline (like a construction line). The budget chip is mono and turns danger-coloured when the budget is reached.
- **Tool and worker cards**: 1 px frame, status dot (green done, pulsing red running), mono summaries and timings; workers get a blue left rule.
- **Model card**: render on a canvas-coloured panel with crosshair marks, red corner ticks on the card, version badge (`v2 · latest` red, `edited by you` blue), a spec strip (Parts / Colours / Size) like a title block, and the "Changed since v1:" line with a red rule and mono part numbers. Actions are joined button groups.
- **Budget stop**: danger frame with a 3 px left edge and a full gauge line.
- **Settings**: provider cards with state badges, numbered sections (01, 02, 03) like drawing callouts, model rows with a mono price line.
- **Editor**: toolbar with a segmented Select/Move/Rotate, parts palette (search by code, colour swatches), canvas with rulers in studs, XYZ gizmo and HUD chips, inspector with mono coordinate fields. The current editor has no palette or inspector; those two panels are a proposal, not a restyle.

## What changes per screen vs today
- **Home**: same copy and four prompts; prompts become labelled "PROMPT A-D" cards on the grid, logo mark above the title, a sheet caption.
- **Chat**: the user bubble gets a hard 1 px border and a squared corner instead of a soft fill; agent messages get a small logo avatar; model card and budget stop as above. Layout and information are unchanged.
- **Settings**: same sections, numbered, key state shown as outlined mono badges.
- **Editor**: viewer chrome restyled; palette and inspector added (new work, not just CSS).
- **My Models / My Parts** (not mocked): they reuse the model card, panel, badge and button styles, so they follow automatically; the parts list should show part codes in mono.

## Accessibility (WCAG 2.1 AA, computed from the token values)
Text contrast ratios on bg / surface / surface-2:

| Pair | Dark | Light |
|---|---|---|
| body `--fg` | 15.8 / 14.8 / 13.4 | 13.8 / 15.1 / 12.8 |
| `--fg-2` | 10.5 / 9.8 / 8.9 | 8.4 / 9.1 / 7.8 |
| `--muted` | 6.9 / 6.5 / 5.9 | 4.9 / 5.3 / 4.5 |
| accent as text | 6.2 / 5.8 / 5.2 | 4.9 / 5.4 / 4.6 |
| ok / warn / danger / info | all 7.3-10.4 | ok 5.1-6.0, warn 5.1-5.5, danger 5.5-6.4, info 6.5-7.6 |
| white on `--accent-solid` | 4.6 | 5.6 |

Everything is AA for normal text. Light `--muted` on surface-2 (4.52) and light accent text on surface-2 (4.57) pass with almost no margin, so do not make them lighter. Input and button borders (`--border-strong`) are 3.2:1 (dark) and 3.1:1 (light) against the page, meeting the 3:1 non-text rule. The decorative card hairline `--border` is deliberately lower (about 1.5:1), so cards never carry meaning on their own (text and badges do). Focus is a 2 px accent outline, and `prefers-reduced-motion` disables the pulse animations. The 10.5 px small-caps labels are the weakest point for legibility: keep them to short labels, never for content.

## Effort to apply
The old variable names are kept as aliases (mapping at the bottom of the palette section in `tokens.css`), so the first step is cheap.
1. `styles/theme.css` (3.7 KB): replace the variable blocks with `tokens.css`, add fonts and base rules. About 1 hour, and the whole app is already roughly re-skinned.
2. `styles/layout.css` (5.8 KB) and `chat.css` (20 KB): sidebar, composer and budget chips, tool/worker cards, budget stop, bubbles. Mostly property swaps (radius, borders, mono on numbers); about 3-4 hours.
3. `model-card.css`, `settings.css`, `providers.css`, `parts.css`, `modal.css`, `responsive.css` (about 20 KB total): about 3 hours, plus a few `<span className="mono">` additions for part codes and costs in `ModelCard.tsx`, `Parts.tsx` and `ProviderSettings.tsx`.
4. `app/web/viewer/*.css` (viewer-common, viewer, editor): restyle the bar and buttons, 2 hours. The palette and inspector are new features and out of scope here.
5. QA in both themes and on a phone: 2 hours.

Total: roughly 1.5 to 2 working days for the restyle, no new dependencies, and no TSX changes beyond those few mono spans. The grid background is one CSS class on the main area.

## Honest notes
Strengths: distinctive, fits the product (part codes, versions and costs are the content), red stays meaningful, both themes work, cheap to adopt. Weaknesses: the technical voice ("Sheet 01", dimension lines) can tip into gimmick if overused, so keep it to page heads; monospace on every number makes dense tables a little busier; full effect depends on web fonts; the dark grid is subtle and may vanish on poor displays (harmless).
