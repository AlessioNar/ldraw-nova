# Studio: a calm, light-first design for Open Brick Designer

**The idea.** Treat the app like a professional design tool: a warm off-white canvas, ink-grey text, 1px hairlines and almost no decoration, so the 3D model is the only loud thing on screen. The model gets the big stage on the right of every chat; the conversation becomes a narrow, quiet column beside it. Brick red appears only where it means "do this" (primary button, send, live dot, version badge) and in the logo.

Mockups (open in a browser; add `?theme=dark` or `?theme=light` to force a theme, `?rail=1` to collapse the sidebar):
`home.html`, `chat.html`, `settings.html`, `editor.html`. Screenshots at 1280x800 (light and dark) and home at 390x844 are in `screenshots/`. (The phone shots show the 390x844 page inside a rounded frame, because headless Chrome will not render a window narrower than about 500px.) The mockups reuse the real copy (suggestion prompts, "v2 · latest", "edited by you", "Changed since v1: ...", "Continuing from v2: ...", "Budget €2 per request", "Stopped: budget reached"). The car render is a dark-background PNG from `docs/plans/done/deterministic-first/screenshots/`; it is framed as a tile, so the app should keep rendering previews on a neutral dark or transparent background.

## Palette and type

| Role | Light | Dark |
|---|---|---|
| Canvas `--bg` | `#faf9f7` | `#131211` |
| Surface `--surface` / well `--surface-2` | `#ffffff` / `#f3f1ed` | `#1a1918` / `#222120` |
| Hairline `--line` | `#e6e2dc` | `#2e2c2a` |
| Ink `--ink` / secondary `--ink-2` | `#1f1d1a` / `#66615a` | `#f1eee9` / `#aaa49b` |
| Brand `--brand` | `#d3342a` (logo was `#d9382e`, nudged darker for AA) | `#ec5a4e` |
| 3D stage `--stage` | `#f1efeb` + 40px grid | `#181716` |

Greys are warm (a touch of yellow), which keeps a light UI from looking clinical and sits well next to LEGO's saturated colours. Status colours (ok, warn, danger, info) are the same hues as today, retuned per theme.

Font: **Inter** (Google Fonts, with a `system-ui` fallback) for UI and **JetBrains Mono** for tool output, part ids and coordinates. Inter has tabular figures, which matters for EUR amounts, part counts and versions, and it is the face people associate with Linear and Figma. Scale: 11.5 / 12.5 / 14 / 15 / 18 / 24 / 34 px; body is 14px, weights 450/500/600 only. If network fonts are unwanted, delete the `@import` and the system stack takes over.

Spacing is a 4px scale (`--s-1` to `--s-8`), radii 4/6/8/12/16/pill, three soft shadows (cards barely lift; only floating panels and the composer get real depth), motion is 90-260ms with one ease curve. All in `tokens.css`, which also lists the mapping from today's names (`--accent` to `--brand`, `--fg` to `--ink`, `--muted` to `--ink-2`, `--border` to `--line`, `--user-bubble` to `--bubble`, ...). Legacy aliases are defined, so existing CSS keeps working while you migrate file by file.

## Components

- **Buttons**: white with a hairline and 1px shadow; `primary` is the only red fill; `ghost` for toolbars. 30px high, 8px radius.
- **Sidebar**: warm grey (`--surface-2`), 264px, or a 52px rail (shown on chat so the stage gets the width). New chat carries a shortcut hint. Active chat is a white card with a hairline. Settings is pinned at the bottom under a rule.
- **Composer**: one large rounded card with a soft shadow; the focus state is a red 3px ring. Model, budget, and mode are quiet chips in a row. "Continuing from v2: ..." is a blue info chip above the input; the budget chip turns red when the last request hit its budget.
- **Chat messages**: no bubble for the agent (plain text on canvas); the user's message is a warm grey bubble. Tool steps are slim collapsed cards with a green tick, tool name, mono argument and duration.
- **Model card**: wide 2:1 render, version badge (red-soft pill) next to "edited by you" (neutral pill), a bold "Changed since v1:" line, quiet download ghosts on the right.
- **Budget stop**: white card with an amber left rule; "Continue with +€2" is the single red action.
- **Stage**: the model gets a gridded canvas with floating, blurred toolbars (title and version at top left, reset and editor at top right, 3D View / 3D Player segmented control at the bottom).
- **Settings**: 2-column provider cards with status pills and a one-line "Stored as ..." note, then a hairline-divided models list, then roles.
- **Editor**: a 48px toolbar (segmented Select / Move / Rotate, undo/redo, Publish as the only red button), parts palette left (2-col tiles), inspector right (position/rotation fields in mono, colour swatches), status bar below.

## What changes per screen vs today

- **Home**: from a dark centred chat box to a light, spacious start page: bigger headline, composer first, the 4 suggestions as quiet cards below it instead of pills above. Composer chips replace the denser settings row.
- **Chat**: from a single column to a workspace: the 3D stage takes about two thirds of the width, chat is about 470px. This is the biggest structural change (new layout, the viewer would be embedded rather than a modal, `ViewerModal` stays for phones). Everything else is restyling.
- **Settings**: same content; flatter cards, less red (today's red accents become neutral or green status pills).
- **Editor**: viewer/editor.css gets the same tokens; toolbar grouping and a proper inspector panel are new chrome.
- **My Models / My Parts** (not mocked): they inherit model-card and list styles; no layout change.

## Accessibility (WCAG 2.1 AA, computed with the standard relative-luminance formula)

| Pair | Light | Dark |
|---|---|---|
| Ink on canvas | 15.98 | 16.17 |
| Ink on surface | 16.81 | 15.17 |
| Secondary ink on canvas / surface | 5.83 / 6.14 | 7.57 / 7.10 |
| Secondary ink on well (`--surface-2`) | 5.44 | 6.50 |
| Button text on `--brand` | 4.89 | 5.73 (dark text on light red) |
| `--brand` as UI element vs canvas (needs 3:1) | 4.65 | 5.47 |
| Brand text/links on surface | 6.25 | 7.10 |
| Brand text on brand-soft badge | 5.44 | 6.19 |
| ok / warn / danger text on their soft fills | 5.42 / 5.65 / 5.59 | 8.53 / 8.20 / 6.68 |

All text pairs pass AA (4.5:1). `--ink-3` (3.5 light, 4.2 dark) is reserved for decorative icons and must not carry text. The original `#d9382e` with white text is 4.61:1, a bare pass that the hover state would break; the brand is nudged to `#d3342a` (4.89:1) for headroom. Link and badge text uses the darker `--brand-text`. Input borders use a dedicated `--field` token at 3.46:1 light and 3.6:1 dark (WCAG 1.4.11); hairlines (`--line`) are decorative dividers and intentionally lighter. Focus is a 2px red-tinted outline on every control. Known gap: the stage floating panels use a translucent surface, so their text contrast depends on what is behind them; give them an opaque fallback.

## Effort to apply

Roughly 2 to 3 days for the restyle, plus 1 to 2 days for the stage layout.

- `styles/theme.css` (3.8 KB): replace with `tokens.css` plus the base rules from `studio.css` (buttons, inputs, badges). Half a day; the aliases make everything else keep working.
- `styles/layout.css` (5.8 KB) and `responsive.css`: sidebar, rail, workspace grid. Half a day, plus the two-pane chat layout (1 day, touches `ChatPage.tsx` to embed the viewer).
- `styles/chat.css` (20 KB, the big one): composer, messages, tool and worker cards, chips, budget stop. About a day.
- `styles/model-card.css`, `settings.css`, `providers.css`, `parts.css`, `modal.css`: mostly token swaps and radius/shadow tweaks, half a day together.
- `app/web/viewer/editor.css` (+ `editor.html`): own variables today; map to tokens and add the palette/inspector chrome. About a day.
- Font: add the Inter link in `index.html` (or self-host it to keep the app offline-capable).
- No component logic changes beyond the chat layout; no new dependencies.
