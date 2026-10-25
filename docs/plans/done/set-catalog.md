# Offline catalog of real LEGO sets

`ldraw_tools/data/set-catalog.bin` holds, for about 20,000 official sets, the Rebrickable name, year, theme path, piece count and the full physical parts list in LDraw terms (part, colour, quantity), but no geometry. Purposes: tell a user with an uploaded inventory "you could build set X" (`ldraw-agent sets buildable`), and let the agent read real set inventories as parts-usage references. Sets that also exist as a bundled LDraw model (`data/models-annotated`, matched by set number) are flagged `has_model`, so a UI can tell "has geometry" from "parts list only".

## Source and terms

- Source: Rebrickable database downloads, https://rebrickable.com/downloads/ (per-file `https://cdn.rebrickable.com/media/downloads/<name>.csv.gz`, or the all-in-one ZIP / TAR.GZ).
- Terms, as supplied by the repository owner from the downloads page on 2026-10-21 (the page itself is behind a Cloudflare challenge for scripted clients, and was not scraped): "The catalog of official LEGO sets is available for bulk download below. Free for personal and commercial use, provided you acknowledge Rebrickable as your data source. ... You may automate downloading these archives at most once a day. Scraping our web pages is strictly against our Terms and will get you banned."
- Consequences: committing the derived catalog is allowed; the acknowledgement "Data from Rebrickable, rebrickable.com" is in `ATTRIBUTION.md` and the README, with the LEGO trademark notice; the builder never downloads anything (CI, the image build and the tests never contact Rebrickable); no web page is scraped.

## Refresh

Download by hand (at most once a day), gunzip into one folder `RB_DIR`: `sets, inventories, inventory_parts, inventory_sets, inventory_minifigs, themes` (plus `colors, parts, part_relationships` only when refreshing the mapping with `tools/build_rebrickable_maps.py`). Unpack the LDraw `complete.zip` and pass its `ldraw` folder:

```
python3 tools/build_set_catalog.py RB_DIR LDRAW_DIR --source-date YYYY-MM-DD          # rewrites ldraw_tools/data/set-catalog.bin
python3 tools/build_set_catalog.py RB_DIR LDRAW_DIR --source-date YYYY-MM-DD --check  # exit 1 if the committed file differs
```

`--check` compares decoded content (zlib output may differ between builds); two builds in one environment are byte-identical. The builder needs the repository's Python environment because it reuses `ldraw_tools.inventory` for the mapping.

## What a set's parts list contains

- Highest Rebrickable inventory version of the set (`inv_version` stored).
- Spare parts excluded. Minifigs (`inventory_minifigs`) and sub-sets (`inventory_sets`) expanded recursively, multiplied by their quantities; cycles are cut. This is the full physical bill: summed mapped plus dropped pieces equal Rebrickable's `num_parts` for 98.8% of sets.
- Translation through exactly the logic of `inventory import` (`inventory._ldraw_part`, `rebrickable-*.json`, with the LDraw library deciding which part files exist). Pieces whose part has no LDraw file (`dropped_part`) or whose colour is unmapped, e.g. "any colour" 9999 (`dropped_colour`) are counted per set instead of listed, so a consumer can show the caveat.
- Dropped as noise (from `themes.csv` only): root themes Gear and Books (8,016 sets), and 437 sets with no non-spare piece or no inventory (e.g. random minifigure bags). Everything else stays, including Duplo and Modulex, whose unmapped share is high but recorded.

## Format

One file: magic, a zlib-compressed JSON header (meta, sorted part table, theme table, one row per set with name, year, theme, piece count, inventory version, mapped and dropped piece counts and the blob offset), then one zlib blob per set holding varint streams (line count, part-index deltas, colour codes, quantities). Details in the `ldraw_tools/sets.py` docstring. Random access by set number reads one blob.

Measured on 20,094 sets, 1,374,220 set lines (39,400 distinct part/colour pairs, 5,615 parts), 4.43 MB of raw varints:

| layout | size |
|---|---|
| chosen: per-set zlib blobs + zlib JSON header (0.31 MB for ~1.4 MB of metadata) | 3.76 MB total (3.28 MB blobs) |
| per-set zlib with a 32 KB preset dictionary | 3.28 MB blobs (no gain) |
| lzma per block of 16 / 64 / 256 sets | 2.73 / 2.52 / 2.44 MB blobs |
| lzma over everything (no random access) | 2.31 MB |

Per-set zlib costs about 1 MB more than the best block scheme but keeps access to one set trivial and the file at a third of the 12 MB budget, so it was kept. Loading the header takes 0.05 s; decoding every set into an inverted index (needed once per process for ranking) 1.4 s.

## Ranking

`sets.rank_sets` scores each set with the measure `inventory.check_model` uses, `sum(min(needed, owned)) / sum(needed)`, over the set's mapped parts. It walks an inverted index from (part, colour) to (set, qty), so only owned lines are visited: 0.11 s per query once the index exists (1.4 s to build it), against 1.3 s for a plain per-set loop; both paths return identical rankings. Results carry `dropped_pieces` and `mapped_share` for the unmapped caveat and `has_model`.

## Local catalogue (other brands)

The backend merges an optional `data/local/set-catalog-local.json` (override: `LDRAW_NOVA_LOCAL_SET_CATALOG`) into the set catalogue at load time. It is written by `tools/fetch_cada_sets.py`, is gitignored and never committed. Format: `{"format": "set-catalog-local/1", "sets": [{"key": "cada:C51026W", "brand": "cada", "set_num": "C51026W", "name": ..., "year": null|int, "pieces": null|int}]}`.

- A local set's `set_num` in the API is its key (`cada:C51026W`), so numbers cannot clash across brands; `number` is the bare number and `brand` the display name (`CaDA`; LEGO sets read `LEGO`). Search matches number, name and brand; `/api/catalog/sets/C51026W` finds it by bare number too.
- Local sets carry no parts list. Adding one creates a set source with its brand and piece count and no rows; the parts come in through the BrickLink import.
- A missing, unreadable or wrongly formatted file is ignored: the catalogue behaves exactly as without it.

## Not verified

- Whether the Rebrickable terms contain further conditions on the downloads page than the quoted text.
- Part print/pattern collapsing (a printed part counts as its unprinted LDraw base) and approximate colours (`exact: false`) are inherited from the mapping; completeness is therefore an estimate of "same shapes", not of identical prints.
