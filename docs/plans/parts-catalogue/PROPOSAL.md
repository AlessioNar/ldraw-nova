# Parts catalogue: turning "My Parts" into real collection management

Proposal only; no app code changed. Base: master at 58b8149f. Date: 2026-10-24.
Owner request: "allow also for a better management of my lego parts catalogue" (Brickit's own management is not good enough).
Product rules that shape everything below: deterministic first (parsers, catalogue lookups, counting, exports); an LLM only at the margin, to read free text such as "add 20 red 2x4 bricks"; cost and time matter, so nothing here needs a model call to work.

---

## 0. What exists today (so the plan evolves it, not replaces it)

| Piece | Where | What it does today |
|---|---|---|
| Inventory = one immutable import | `ldraw_tools/inventory.py` (`Row{part,colour,qty,rb_part,rb_colour}`, `Inventory`, `parse_rebrickable_csv`, `combine`, `match_bom`, `check_model`) | Rows are LDraw part number + LDraw colour code + owned qty. `unmapped_parts` / `unmapped_colours` are kept beside `rows`. Only Rebrickable CSV is parsed. |
| Storage | `app/web/backend/inventories.py`, files `<INVENTORIES_DIR>/<id>.json` + `<id>.csv` | Import via `ldraw_tools.cli inventory import` subprocess in the sandbox; only list, detail (search part/description, one colour, 50/page), rename, delete. No edit, no update, same-name upload is a 409. |
| API | `routers/inventories.py` | `GET/POST /api/inventories`, `GET/PATCH/DELETE /api/inventories/{id}`, `GET /api/inventories/buildable[/status|/warm]` |
| Buildable | `ldraw_tools/buildable.py` (BOM cache per model, `rank`, `evaluate`), `backend/buildable.py`, `components/parts/BuildablePanel.tsx` | Ranks the bundled set models by share covered; about 6 s per query, first scan minutes. |
| Sets catalogue | `ldraw_tools/sets.py` (`search_sets`, `get_set`, `set_bom(set_num) -> {(part,colour): qty}`, `rank_sets`), `data/set-catalog.bin`, CLI `sets` | Offline catalogue of real sets with exact parts. **No web router and no UI use it yet.** |
| Parts steering | `backend/parts_steering.py`, `parts_mode` off/prefer/only + `inventory_ids` per chat | Prompt block, parts check card (`PartsCheckCard.tsx`) after a build. |
| Editor palette | `routers/editor_palette.py` (`owned_map`, `used_map`, `check_add`, `palette_items`) | Available = owned minus already placed in the model being edited; refused in `only` mode. This is the "taken off available parts when used" behaviour. |
| Render | `backend/render.py` (`render`, `render_snapshot`) | Renders an LDraw file to PNG (SwiftShader here, so seconds, not milliseconds). |
| UI | `pages/Parts.tsx`, `components/parts/{UploadDialog,ImportReportView,InventoryDetailView,BuildablePanel,PartsControl}.tsx` | Tabs "Parts lists" and "What can I build?". |

Journey audit (`docs/plans/journeys/02-my-parts-and-editing.md`, Journey 3) already lists the gaps this proposal answers: F3-1 dead-end parts check, F3-2 substitutions invisible, F3-5 no pictures, F3-6 only Rebrickable, F3-7 numbers do not reconcile and colours shown as numbers, F3-8 thin browsing and no colour-name search, F3-9 no update/replace and delete breaks chats.

Facts that constrain the design:
- All consumers (`combine`, `match_bom`, `owned_map`, `parts_steering.load_inventory`, the BOM cache ranking) read `rows` as exact (part, int colour, qty). **Approximate or location data must therefore never appear as a normal row**; approximate data lives beside `rows`, like `unmapped_*` does, so nothing downstream breaks.
- `data/rebrickable-colours.json` maps Rebrickable colour ids to LDraw codes and names; it has no BrickLink colour ids. BrickLink import/export needs one more generated table (see 3.3).
- The backend's system Python cannot import `ldraw_tools`; heavy parsing runs as a CLI subprocess. Small edits (add 20 parts) must stay in the backend (plain JSON), or every click costs a sandbox spawn.

---

## 1. Jobs to be done

A person with a real, growing collection (the owner: 798 pieces scanned so far, more to come, several sources) needs to:

| # | Job | Today | Wave |
|---|---|---|---|
| J1 | Add what I own, fast: a whole set by number, a Brickit scan, a typed "20 red 2x4", a spreadsheet | Rebrickable CSV only | 1, 2 |
| J2 | Keep it correct: fix a count, undo a mistake, re-import a newer scan without duplicating or breaking chats | Delete and re-upload; chats break | 1 |
| J3 | Find a part: by name, number, size, category, colour name, location; see a picture | Part id + description text only | 1 |
| J4 | Know what I can build now / nearly | Exists, slow, no pictures | 3 |
| J5 | Know what I am missing for a model, and buy it | "Short" card, dead end | 3 |
| J6 | Keep parts reserved for models I built so they do not show as free | Not modelled | 3 |
| J7 | Organise physical storage: "where is the 2x4 red?" | Not modelled | 2 |
| J8 | Get my data out (wanted list, CSV), and not be locked in | None | 2 |

Non-goals: pricing and store comparison, a marketplace, syncing accounts with Rebrickable/BrickLink APIs (all need keys and network; exports cover the need), multi-user.

---

## 2. Data model: collection, sources, rows

### 2.1 Idea in one paragraph

Keep "inventory" as the unit on disk. Call it a **source** in the UI. The **collection** is not a new store; it is the sum of the owner's chosen sources, which is exactly how `inventory_ids` already works for chats. Add one new editable source kind, **Loose parts** (id `loose`, created on first use), for manual and quick adds, and let any source carry per-row **location**. Sources keep their original upload (`.csv`) for audit. Nothing existing is rewritten.

### 2.2 Source file (`<id>.json`), additive fields

Existing keys stay (`id, name, created_at, source_filename, rows, unmapped_parts, unmapped_colours, warnings, totals, import`). New optional keys; a file without them reads as today:

```jsonc
{
  "schema": 2,                         // absent == 1
  "kind": "import" | "set" | "loose",  // absent == "import"
  "set": {"set_num": "8448-1", "copies": 1, "state": "built"|"parts"},   // kind == "set"
  "origin": {"format": "brickit-pileometer", "scan": "2026-10-20", "file": "x.csv"},
  "rows": [{"part":"3001","colour":4,"qty":12,"rb_part":"3001","rb_colour":4,
            "loc": "bin-red-bricks"}],      // loc optional; same (part,colour) may appear with several loc
  "review": [                           // approximate items; NOT counted anywhere until resolved
    {"id":"r1","part":"3001","family":"Reds","candidates":[4,320,25],
     "qty":6,"brickit_part":"3001","note":"scan 2"}
  ]
}
```

Why this shape:
- `rows` stay exact `(part, int colour)`, so `combine`, `match_bom`, `owned_map`, `check_model` and the BOM cache need **no change**. A tiny `combine` tweak sums over `loc`.
- `review` is the same pattern as `unmapped_*`: parsed, kept, shown, never silently counted. Resolving an item (pick a colour, optionally split the quantity across colours) moves it into `rows` and logs an op.
- `kind: "set"` sources are materialised from `sets.set_bom()` at add time (rows copied), so a later catalogue refresh cannot change what the owner owns. `copies` multiplies; `state: "parts"` means taken apart, `"built"` means the parts are inside a built set (still counted as owned, flagged "assembled" in the UI; see reservations).

### 2.3 Locations

`<INVENTORIES_DIR>/locations.json`: `{"locations":[{"id":"bin-red-bricks","name":"Red bricks","parent":"drawer-3","note":""}]}`. A flat list with an optional parent gives a two-level tree (Cabinet > Drawer > Bin) with no schema work. Rows reference by id. A row without `loc` is "Unsorted" (the default for every import). Moving parts between bins is an op (2.5), not a rewrite.

### 2.4 Reservations

`<INVENTORIES_DIR>/reservations.json`:

```jsonc
{"reservations":[{"id":"res-1","model":"azure-dart-v1","chat":"20261023-174459-0526d4",
  "state":"built","created_at":0,"rows":[["3001",4,12],["3023",15,4]],"source":"bom"}]}
```

Rows come from the model's physical BOM (`export_bom`, already produced per model). **Available = owned − reserved (sum over active reservations)**, computed in one function `available_map(ids)` that `editor_palette.owned_map` and the buildable ranking call. Reserving never mutates a source, so releasing is deleting the reservation. A reservation may exceed ownership (the owner built it with parts since lost); the UI shows that as "short by N" rather than hiding it.

### 2.5 History and undo

Each editable change appends one line to `<id>.log.jsonl` (append-only, next to the json): `{ts, batch, op, part, colour, loc, delta | qty_before->qty_after, note}`. A **batch** groups one user action (an import, a quick add of 5 lines, a move). The json is the materialised current state; the log is the audit and the undo stack. `POST /api/collection/undo {batch}` applies the inverse ops. Keep the last 500 batches per source (the log is cheap text). A replace-import writes one batch with every diff, so "I re-uploaded the wrong scan" is one undo.

### 2.6 Migration (no rewrite)

1. Reader: `inventories._load` already returns the dict as-is; add defaults for `schema`, `kind`, `review`, `loc` at read time (`setdefault`). Old files stay untouched until their first edit; on first edit the file is saved with `schema: 2`.
2. `summary()` gains `kind`, `review_count`, `locations_used`. Existing fields are unchanged, so `Parts.tsx` keeps working while the new UI lands.
3. Stable id on replace: re-uploading to an existing source (`POST /api/inventories/{id}/replace`) keeps the id, so chats with `inventory_ids: [id]` keep working (fixes F3-9). `?dry_run=1` returns the diff (+/− per part) before committing.
4. `loose` is created lazily; no data move. No existing inventory is converted to anything.
5. Rollback: delete the new keys/files; v1 readers ignore unknown keys (the toolkit's `Inventory.from_json` already filters to known fields; add `review/kind/set/origin` to its dataclass in wave 1 so the CLI also round-trips them).

### 2.7 Colour exactness

Exact means an LDraw colour code (the existing `colour` int; names from `parts_steering.colour_name`). Approximate means a **colour family** (Pileometer: "Reds", "Grays"). A static table `ldraw_tools/data/colour-families.json` (family name -> LDraw codes, ordered by how common; generated once from the LDraw colour table, hand-checked) provides `candidates`. The resolution UI offers first the candidates already present in the collection or wanted by a model being built, then the rest. Buildable/shortage may optionally treat a review item as "could be any candidate" (shown as a separate "maybe" count, never mixed into the exact count).

---

## 3. Ways in

All writes go through one backend module `app/web/backend/collection.py` (new) with `apply_ops(source_id, ops, note) -> batch`, so every way in gets history, undo and validation for free. All parsers are deterministic Python; none calls a model.

### 3.1 Add a whole set by number (wave 1, S-M)
- `GET /api/catalog/sets?query=8448&limit=20` wraps `sets.search_sets`; `GET /api/catalog/sets/{num}` returns metadata and part count. The catalogue is static, so read it once through a tiny exporter CLI `ldraw_tools.cli sets export --json` and cache the result in `data/cache/sets.json`, rather than spawning a subprocess per keystroke.
- `POST /api/collection/sets {set_num, copies, state}` materialises `set_bom(set_num)` into a `kind:"set"` source named "8448 <set name>". Pieces whose Rebrickable part has no LDraw equivalent are reported as import does today (`dropped_part`, `dropped_colour` in the catalogue meta).
- UX: type "8448" or "tipper truck", see name, year, piece count, a thumbnail if a bundled model exists, click Add. No typing of parts: the fastest path for boxed sets.

### 3.2 Imports (wave 1 for the registry and CSV dialects, wave 2 for the rest)

One registry, same pattern as `generators`: `ldraw_tools/importers/` with `detect(text, filename) -> format | None` and `parse(text) -> ParseResult` (the existing dataclass, extended with `review` and `origin`). Upload auto-detects; the dialog shows "Detected: BrickLink wanted list XML" and lets the owner override.

| Source | Format | Notes | Wave | Effort |
|---|---|---|---|---|
| Rebrickable parts list / database CSV | existing | move `parse_rebrickable_csv` behind the registry | 1 | S |
| Brickit / Pileometer CSV | Rebrickable-like CSV with colour families, Brickit part ids, multiple scans | Being built by another agent. In this design it is an importer that emits `rows` for exact colours and `review` items for families, with `origin.scan`. Each scan is its own source by default (a bad scan is deletable), or merged into an existing one by choice. "Unknown"/"Unrecognized" placeholders go to a separate `unidentified` count, not to "Rebrickable has no LDraw equivalent" (F3-7). | 1 (hook) | M (this repo's part) |
| BrickLink XML (wanted list / inventory) | `<INVENTORY><ITEM><ITEMTYPE>P<ITEMID><COLOR><QTY>` (also `MINQTY`, `QTYFILLED`) | Needs BrickLink colour id -> LDraw colour (3.3) and BrickLink part id -> LDraw part (mostly identical numbers; reuse the `rebrickable-parts.json` aliases). `QTYFILLED` is what is owned in an inventory export. | 2 | M |
| BrickLink CSV (`Item No,Color,Qty,Condition`) | CSV with BrickLink colour names | Names mapped by name via the same table; today it fails with a header error (F3-6). | 2 | S |
| BrickStore BSX | XML (`<BrickStoreXML><Inventory><Item>`), same item fields as BrickLink | Cheap once BrickLink XML works: one more root tag. | 2 | S |
| Generic CSV | any header | Column-mapping step (choose which column is part / colour / qty), remembered per header signature. Covers spreadsheets. | 2 | M |

Import is always **preview first**: counts, new vs changed lines, review items, unmapped lines with reasons, then Commit (one batch, undoable). Import goes into a new source, into `loose`, or **replaces** an existing source (diff shown).

### 3.3 Colour and part tables (dependency of 3.2 and 3.6)
`ldraw_tools/data/bricklink-colours.json`: BrickLink colour id and name -> LDraw code, generated from Rebrickable's `colors.csv` `external_ids` (the same upstream file the existing `rebrickable-colours.json` came from; check the generator script in next to the data). One-off, S. The same table gives the reverse mapping for export.

### 3.4 Quick manual add with search and images (wave 1, M)
- Single input: type "2x4 brick red", "3001", "wheel". Deterministic search over `ldraw.parts_index()` (already used for descriptions), extended with size-token parsing (`2x4`, `2 x 4`, `4x2` normalised to sorted dimensions) matched against the description, and colour-name matching against the LDraw colour table, so "red" works (F3-8). Ranking: exact id, then all tokens, then prefix; owned parts first.
- Each hit shows a **thumbnail**, description, category and colour chips (colours already owned first, then common colours, "other..." opens the full table with swatches from `ldrawColours.ts`).
- Click a chip to add +1, type a number (`+10`), Enter commits as one batch with an "Undo" toast. No modal per add.
- **Thumbnails**: `backend/part_images.py`, `GET /files/part-thumb/{part}/{colour}.png`. A one-part LDraw file rendered with the existing `render.render`, cached under `data/cache/part-thumbs/`. Rendering is software and takes seconds, so: lazy on first view, a queue of 2 workers, a neutral placeholder (the part number) until ready, and a background warm of the owned parts after import (798 pieces / 225 parts is about 225 renders, once). Option to cut the work: render once in neutral grey and tint client-side in the list, and render the real colour only in the detail view. A static fallback (Rebrickable CDN images) needs the network and is not offline-safe; offer it only as an opt-in.

### 3.5 Natural-language quick add (wave 2, S-M, optional and priced)
Two stages, the LLM only when stage 1 cannot parse:
1. **Grammar parser** (deterministic, free, instant): `qty? colour? size? shape-word` per clause, split on commas / "and" / newlines: "20 red 2x4 bricks, 8 black 1x2 plates, 4 wheels" resolved against the same search as 3.4 (size + colour + shape word -> candidate parts; ambiguous results become a pick list, never a guess).
2. **Fallback**: one structured call (schema `{items:[{text, qty, colour?, part_hint?}]}`) with the cheapest configured model, no tools, no agent loop, an input of a few hundred tokens: roughly EUR 0.001 per use with a Haiku-class model (to be measured, not assumed). The model only extracts fields; the same deterministic resolver picks the part and a preview table shows what will be added. The UI says "Understood without AI" or "AI used (about EUR 0.001)", and the spend goes into the existing usage ledger (`usage.py`). Off unless a key is configured. Nothing is written until the owner presses Add on the preview.
Follows the `understand.py` pattern (single call, golden file for evaluation: `understand_golden.json`); a new `quick_add_golden.json` of about 30 phrases measures stage-1 coverage before any model is wired in.

### 3.6 Exports (wave 2, S-M)
- **BrickLink wanted-list XML**: `<INVENTORY><ITEM><ITEMTYPE>P</ITEMTYPE><ITEMID>3001</ITEMID><COLOR>5</COLOR><MINQTY>8</MINQTY></ITEM>...` from any shortage list or from the whole collection. Uses 3.3 for the colour id; lines without a BrickLink mapping are listed in the download dialog and omitted, never guessed.
- **Rebrickable CSV** (`Part,Color,Quantity`, Rebrickable colour id from `rb_colour` or the reverse map) for a source or the whole collection (so the owner is never locked in).
- Plain CSV with names, locations and quantities for spreadsheets.
- Endpoint: `GET /api/collection/export?format=bricklink-xml|rebrickable-csv|csv&scope=collection|source:<id>|shortage:<model>`.

---

## 4. Browsing

New page structure (replaces the "Parts lists" tab; "What can I build?" stays as a tab): **Collection**, **Sources**, **Review**, **Reserved**, **What can I build?**.

- **Collection tab**: every row of the selected sources, grouped by part (one row per part with colour chips and a total), with a Group by switch: Part (default) / Colour / Location / Category / Source. Header: "798 pieces, 225 parts, 6 colours, 17 to review".
- **Search and filters**, one endpoint: `GET /api/collection/parts?q=&colour=&category=&size=&loc=&source=&min_qty=&sort=&limit=&offset=`. Free text `q` uses the 3.4 resolver (id, description tokens, size, colour name). Filter chips for colour (all colours, not the top 12), category (first LDraw category word such as Brick, Plate, Tile, Slope, Technic, from `parts_index`), size (for example 2x4), location, source, "has review items". Sorts: quantity, part number, name, recently added. Server pages of 100; the client list is virtualised.
- **Part detail** (drawer): large render, description, owned per colour and per location, reserved per model, change history for the part (from the log), buttons "+/-", "Move to bin", "Add to wanted list".
- **Review tab** (the colour-approximate flow): a queue grouped by family ("Reds, 6 pieces: 3001, 3004..."). Each item shows the thumbnail in each candidate colour; one click assigns all grouped items to a colour; "split" distributes the quantity; "skip" leaves it in the queue, still not counted. A bulk "assign all Reds to Red" for people who do not care about the exact shade. Resolved vs open counts show in the header so the queue visibly shrinks.
- Unmapped and unidentified lines get their own list with the reason ("Pileometer could not identify this", "no LDraw part") and an "I know this one" action that attaches a part number (stored in `part_aliases.json`, applied on later imports).

---

## 5. Connection to building

### 5.1 Buildable from my parts (wave 3, M)
Keep `buildable.rank`; change its inputs and speed:
- Feed it `available_map` (owned minus reserved), not owned.
- Show thumbnails (reference models have a snapshot via `render_snapshot`), set number and name as the title (F3-5), "can build now / missing at most N pieces / maybe with review items".
- Remove the 6 s per-query lag (F3-3): precompute `evaluate` for all models once per collection version (`collection_version` = hash of rows + reservations) into a small SQLite table next to the BOM cache, and filter in memory. Warm automatically after every committed change (F3-10), in the background, cancelling a stale run.
- "Build again" list for the sets in `kind:"set"` sources, showing whether their parts are still complete.

### 5.2 Shortage list and one-click wanted-list export (wave 3, S-M)
For any model (My Models, a chat, a bundled set): `GET /api/collection/shortage?model=<file>&mode=exact|substitute` returns lines `{part, colour, needed, owned, reserved, missing, substitutes:[{colour,available}]}`. It replaces the dead-end `PartsCheckCard` list: each line has a thumbnail and "own N in other colours" (the substitution data F3-1/F3-2 asked for), and the card gets buttons **Export wanted list (BrickLink XML)**, **Copy as text**, **Export CSV**. Same `check_model` core, so the numbers match what the agent sees.

### 5.3 Reserve on built, release on un-build (wave 3, S-M)
- A model gets a state: draft / built (physical). In My Models and on the chat's model card, the button "I built this" shows the shortage (if any) and creates a reservation from the model BOM. "Take apart" or deleting the model removes the reservation. A **Reserved** view lists reservations with a release button per model.
- Effects: `available_map` everywhere (palette, parts check, buildable, parts steering prompt block), so a built Tipper Truck no longer lets the agent plan with the same bricks.
- Sets: for `kind:"set"` sources with `state:"built"` the set shows as one assembled item; "take apart" switches it to `parts`. To avoid double counting, only one of "set built" or "model built from these parts" may reserve a given piece. Whether built sets reserve by default is open question 1.

### 5.4 Editor palette limited to owned parts (wave 3, S)
Already half done (`palette_items` with `owned/used/available`, `only` mode). Add: (a) subtract reservations of other models via `available_map`; (b) a "show only owned" default toggle; (c) when a part is used up, "own N in other colours" with one-click recolour; (d) "Not owned: add to wanted list" instead of blocking in `prefer` mode; (e) a missing or deleted source no longer fails with a bare HTTP 400 (F3-9): the editor says which source is gone and offers to pick another.

---

## 6. UX sketches (structure only, no visual style)

Collection (default tab):

```
My Parts                                        [ + Add parts v ]  [ Export v ]
------------------------------------------------------------------------------
 Collection | Sources (4) | Review (17) | Reserved (2) | What can I build?
------------------------------------------------------------------------------
 [ Search: "2x4 red brick, 3001, wheel"     ]  Colour v  Category v  Size v
 Location v   Source v   [ ] needs review      Group by: (Part) Colour Location
 798 pieces . 225 parts . 6 colours . 17 to review          Sort: Quantity v
------------------------------------------------------------------------------
 [img] 3001  Brick 2 x 4                         total 24   free 20  (4 reserved)
        (Red 12)(Blue 6)(White 6)       Bin: Red bricks, Drawer 3         [+] [...]
 [img] 3023  Plate 1 x 2                         total 40   free 40
        (Black 20)(White 12)(Red 8)     Unsorted                           [+] [...]
 [img] 3010  Brick 1 x 4  ...
------------------------------------------------------------------------------
```

Add parts menu (one entry point for every way in):

```
 + Add parts
   Quick add ............. type "20 red 2x4 bricks" or search a part
   Add a set ............. by number or name (8448, "tipper truck")
   Import a file ......... Brickit/Pileometer, Rebrickable, BrickLink, BrickStore, CSV
```

Quick add (inline, not a modal):

```
 Quick add   [ 20 red 2x4 bricks, 8 black 1x2 plates          ]  Understood without AI
 ---------------------------------------------------------------------------
  [img] 3001 Brick 2 x 4   Red     x20   -> Loose parts / Bin: Red bricks v  [x]
  [img] 3023 Plate 1 x 2   Black   x 8   -> Loose parts / Unsorted        v  [x]
  ? "wheel"  -> 3 candidates:  (3641 Tyre)  (4624 Wheel)  (2814 ...)   pick one
                                                    [ Cancel ]  [ Add 28 pieces ]
 After adding: "Added 28 pieces (Undo)"
```

Import preview (all formats):

```
 Detected: Brickit / Pileometer CSV      scan 2026-10-20      Into: (New source) Replace v
 388 lines . 842 pieces     798 exact     44 to review      0 unidentified
   Exact colours ........ 700 pieces, 190 parts
   Colour families ...... 44 pieces: Reds 12, Grays 20, Blues 12   -> go to Review
   Placeholders ......... 12 "Unknown"  (not parts; counted separately)
   No LDraw part ........ 3 lines [show]
 Changes vs "Pileometer 1":  +14 lines  -3 lines  ~6 changed [show diff]
                                              [ Cancel ]  [ Import ]
```

Review queue (colour approximate):

```
 Review  17 open                                    [ Assign all to most common ]
 Reds (6 pieces, 4 parts)            (Red) (Dark Red) (Salmon)   [Skip]
   [img] 3001  x3     candidates:  [Red] [Dark Red] [Salmon]   or split ...
   [img] 3004  x2     [Red] [Dark Red]  [Salmon]
 Grays (9 pieces)   (Light Bluish Gray) (Dark Bluish Gray) (Light Gray)
```

Part drawer and model shortage:

```
 3001 Brick 2 x 4   [large render]
  Red     12 own   4 reserved (Azure Dart)   8 free     Bin: Red bricks
  Blue     6 own   6 free                               Unsorted [Move]
  History: +10 quick add (20 Oct)  -2 correction  (Undo)

 Azure Dart v1 . 51 parts          [ I built this ]   needs 6 more
  [img] 3641 Tyre   Black   need 4  own 0   (own 6 in Dark Gray: swap?)
  [img] 4624 Wheel  Black   need 4  own 2   missing 2
          [ Export wanted list (BrickLink XML) ] [ Copy list ] [ CSV ]
```

---

## 7. Waves, effort, order

Effort: S under a day, M a few days, L a week or more. Each wave ships on its own and keeps old files and the existing UI working.

### Wave 1: a collection I can maintain (about 2 weeks)
| Item | Effort | Main files |
|---|---|---|
| Source schema v2 read defaults, `summary()` fields, toolkit `Inventory` dataclass round-trip | S | `inventories.py`, `ldraw_tools/inventory.py` |
| `collection.py` ops + log + undo, `loose` source, row qty edit and delete | M | new `backend/collection.py`, `routers/collection.py` |
| Replace-import with diff and stable id (fixes F3-9) | M | `inventories.py`, `routers/inventories.py` |
| Importer registry, Rebrickable moved in, hook for Brickit (the other agent's work plugs in), preview-then-commit, `review` stored | M | new `ldraw_tools/importers/`, `UploadDialog.tsx`, `ImportReportView.tsx` |
| Add a set by number (sets endpoints, `kind:"set"`) | S-M | new `routers/sets.py`, `ldraw_tools/sets.py` export |
| Quick manual add with search (size and colour-name aware), +/- counts | M | `collection.py` search, new `components/parts/QuickAdd.tsx` |
| Part thumbnails (lazy, queued, cached) | M | new `backend/part_images.py`, `render.py` |
| Collection browse: group, filter chips, virtualised list, part drawer | M | new `CollectionView.tsx`; `InventoryDetailView.tsx` replaced |
| Review queue UI and resolve ops | S-M | new `ReviewQueue.tsx` |

### Wave 2: more ways in and out, storage (about 1.5 weeks)
BrickLink XML/CSV and BSX importers (M+S+S), colour/part tables (S), generic CSV column mapping (M), exports: BrickLink wanted XML, Rebrickable CSV, CSV (S-M), locations tree + move + filter (M), grammar quick-add parser with golden phrases (M), optional single-call LLM fallback behind a setting, priced and logged (S-M).

### Wave 3: connect to building (about 2 weeks)
Reservations store + `available_map` into palette, buildable and steering (M), "I built this" / release (S-M), shortage endpoint and card with substitution data + wanted-list export (S-M), precomputed buildable index keyed by `collection_version` with auto-warm (M), palette tweaks (S), buildable cards with thumbnails and set names (S).

### Wave 4 (only if wanted): polish
Bin label printing, minimum-stock alerts, duplicate-scan detection ("this scan is 95% a copy of Pileometer 1"), owned-set completeness check, a mobile pass once the design-system rework lands.

Order rationale: wave 1 removes the data-loss and dead-end problems and is useful with the existing CSV alone; wave 3 depends on wave 1's ops and thumbnails; wave 2 items are independent of each other and can run in parallel with wave 3.

Parallel-safe streams: importers (3.2, 3.3), exports (3.6) and thumbnails (3.4) touch separate files; `collection.py` is the one shared dependency and lands first.

---

## 8. Risks and open questions

Risks:
- **Thumbnail speed**: software renders take seconds each. Mitigation: lazy, cached, optionally one neutral render tinted client-side; measure on the owner's 225 parts before committing to coloured renders.
- **BrickLink/Rebrickable id coverage**: some parts have no LDraw equivalent or differ by print/mould variant (`rebrickable-parts.json` `aliases`, `no_ldraw`). Export omits and lists them; it never guesses.
- **Brickit scan quality**: counts and identifications are noisy, hence preview first, a review queue and per-scan sources so a bad scan is deletable.
- **Materialised set rows can go stale** against a catalogue update; deliberate, and the catalogue meta is recorded in the source.
- **Two writers**: the toolkit CLI (agent sandbox) only reads sources; only the backend writes. Keep the existing `_lock` and atomic writes, and append to the log under the same lock.
- **Scope creep into a marketplace**: excluded above.

Questions only the owner can answer:
1. **What "built" means for availability**: when a model (or an owned set) is marked built, should its parts count as reserved by default? This changes every "what can I build" answer.
2. **Locations**: bins and drawers in the first release, or only a note per part? Storage is the most personal part of the model; a wrong default (two levels, per row) means a data migration later.
3. **Colour families**: when a scan says "Grays", may those pieces count as "maybe" in buildable (labelled separately), or must they stay out of every number until you assign a colour?
4. **Natural-language add**: is a Haiku-class call at roughly EUR 0.001 per use acceptable (off until a key is set), or should quick add stay strictly free and rule-based?
5. **Brickit relationship**: one-way import (scan in Brickit, import here), or retire Brickit so this app is the only home of the collection? The second needs waves 1 and 2 to be solid first.

## Owner decisions (2026-10-24)

1. Marking a model or owned set as built reserves its parts by default.
2. Storage: a free-text note per part (e.g. "blue tub") now; the bins-and-drawers locations tree with "gather by location" lists and bin labels is wanted later. Notes must be convertible into locations then.
3. Colour-family pieces (e.g. Brickit "Grays") count as a separate "maybe" in buildable and shortage answers, never as exact colours, until assigned.
4. Quick add is rule-based only: no LLM fallback.
5. Brickit stays an occasional import source; this app is the home of the collection. Own part detection from photos is a later goal, not planned now.
