# Design direction: Toy box

**Idea.** Open Brick Designer should feel like a tidy play table: warm paper background with a faint stud grid, chunky rounded cards, and buttons that sit on a solid edge and press down when clicked. The four brick colours (red, yellow, blue, green) are used deliberately: red for the one primary action, yellow for "this is the latest / continue / publish", blue for selection and context, green for success and changes. The finished model is the hero: its card gets a yellow border, a "v2 · latest" badge and a few confetti bricks.

Mockups (open in a browser, add `?theme=dark` or `?theme=light`): `home.html`, `home-rail.html` (collapsed rail), `chat.html`, `settings.html`, `editor.html`. Screenshots are in `screenshots/` (1280x800 light and dark, home at 390x844). Styles: `tokens.css` (the system) and `mockup.css` (components for the mockups only).

## Palette and type

| Role | Light | Dark |
|---|---|---|
| Page / card / card-2 | `#fbf6ec` / `#fff` / `#f4ede0` | `#14161f` / `#1d2130` / `#272c3e` |
| Text / muted | `#1d2230` / `#5b6272` | `#f3f1ea` / `#a8aec2` |
| Accent red (text, fill) | `#cf3429` | `#ff7467` text, `#d23a2e` fill |
| Yellow (fills only) | `#ffc933` + ink `#3a2a00` | same |
| Blue / green | `#1a58b8` / `#17703c` text | `#8db7ff` / `#5ddc8b` text |

Brand colours for illustration (`--brick-red/yellow/blue/green`) are separate from the text-safe semantic colours. Dark is a deep navy, not black, so the bricks stay saturated.

Type: **Nunito** (Google Fonts, weights 500-800), a rounded geometric sans that is friendly but has a normal, readable x-height and good numerals for part counts and euro amounts. Fallback is `ui-rounded, "SF Pro Rounded", "Segoe UI", system-ui`. If the app must stay offline or self-hosted, drop the `@import` and ship the woff2 files (about 60 KB for two weights) or accept the system fallback; nothing breaks. Scale 12 / 13.5 / 15 / 17 / 22 / 32 px; spacing 4px base; radii 8 / 14 / 22 / pill; motion 90 ms press, 180 ms with a slight overshoot ease (disabled under `prefers-reduced-motion`).

## Mapping from today's variables

Existing names are kept as-is, so the migration is mostly a value swap:

| Current (`theme.css`) | Toy box |
|---|---|
| `--bg`, `--surface`, `--surface-2`, `--border`, `--fg`, `--muted` | same names, new values |
| `--accent` | same name; used for red text/links. **New** `--accent-fill` for button backgrounds (dark mode needs a deeper red behind white text) and `--accent-edge` for the pressed edge |
| `--accent-soft`, `--accent-fg` | same |
| `--info/-border/-soft`, `--ok`, `--warn/-soft`, `--danger/-soft` | same; added `--ok-soft` |
| `--user-bubble` | same (now soft blue) |
| `--shadow` | same name; add `--shadow-pop`, `--raise`, `--raise-pressed`, `--focus` |
| `--radius` (10px) | same name, 14px; add `--radius-sm`, `--radius-lg`, `--radius-pill` |
| `--sidebar-w`, `--rail-w` | same; rail 48 -> 60px so the logo and 44px buttons fit |
| new | `--brick-*`, `--highlight`, `--highlight-ink`, `--input-border`, `--stud`, `--font-ui`, `--sp-*`, `--text-*`, `--ease`, `--dur*` |

## Components

- **Buttons**: 2px border plus a 3px solid under-edge; `:active` moves down 2px and shrinks the edge. Primary is red, `yellow` is for continue/publish, ghost for icon actions. Focus is a 3px blue ring.
- **Cards and panels**: white, 2px warm border, 14-22px radius, 3px under-edge. Inputs use `--input-border` so field outlines reach 3:1.
- **Chips and badges**: pills. "v2 · latest" is yellow, "edited by you" blue, "Key set" green, "Key rejected" amber, "Budget €2 per request" amber, "Continuing from v2: ..." blue with border.
- **Model card**: render on the left (stud-grid backdrop behind it), facts on the right; "Changed since v1:" sits in a green-edged strip. Stacks vertically on phones.
- **Tool steps**: slim white rows with a small square status brick (green done, pulsing yellow running) instead of a dot.
- **Budget stop**: amber/yellow card with a yellow Continue button. It sits above the composer as today.
- **Sidebar**: cream-white, yellow logo tile, active chat marked with a red left edge, dashed rule above Settings. Collapsed rail shows icons only.
- **Editor**: segmented tool switch (active tool is a blue pressed key), 3-column grid of part tiles, inspector fields, colour swatches that are tiny bricks, a perspective stud floor behind the canvas (the real WebGL scene would draw its own).

## What changes per screen vs today

- **Home**: four coloured stripes on the suggestion cards, a row of four little bricks above the title, stud-grid background. Layout otherwise unchanged.
- **Chat**: bubbles become soft blue with a rounded tail; the model card is the largest, warmest object on screen; budget chip and continuing chip are pills with clear colours instead of grey text.
- **Settings**: provider cards get a colour tile per provider and clearer state badges; model list rows get big switches.
- **Editor**: chunkier toolbar and inspector; same functions and labels.
- **My Models / My Parts** (not mocked): reuse the model card and panels as-is.

## Accessibility (WCAG AA, computed)

| Pair | Light | Dark |
|---|---|---|
| Text on page / card | 14.7 / 15.9 | 16.0 / 14.2 |
| Muted on page / card / card-2 | 5.7 / 6.1 / 5.3 | 8.2 / 7.2 / 6.3 |
| Red accent text on card | 5.0 | 6.1 |
| White on red button | 5.0 | 4.8 |
| Info, ok, warn, danger text on its soft fill | 5.3 - 6.7 | 7.0 - 9.1 |
| Dark ink on yellow | 9.0 | 10.6 |

All text pairs pass 4.5:1. Risks: yellow on the light page is only 1.4:1, so yellow is **never text or a lone indicator on light**; it is always a fill with dark ink and a darker edge. Decorative card borders (about 1.6:1) are not relied on to identify controls; inputs use `--input-border` (3.9:1 light, 3.5:1 dark). The "running" tool brick is yellow, so state is also named in text. Focus rings are blue and visible on both themes. Motion respects reduced-motion.

## Effort to apply

Roughly 1.5-2 days. `theme.css` is a value swap plus new tokens and the button/input rules (half a day). `layout.css` (sidebar, rail, stud background), `chat.css` (bubbles, tool cards, composer, chips, budget stop; the biggest file at 20 KB) and `model-card.css` (horizontal card, badges) take most of the rest. `settings.css`, `providers.css`, `parts.css`, `modal.css` and `responsive.css` need smaller touches, and `app/web/viewer/editor.css`, `viewer-common.css` and `player.css` need the same tokens linked or mirrored (they are separate pages, so they need their own copy of `tokens.css` or a shared link). No component markup needs to change except the logo tile and the home brick row. Add the font as a self-hosted file if the app should work offline.
