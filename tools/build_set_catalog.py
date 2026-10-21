#!/usr/bin/env python3
"""Build the offline catalog of real LEGO sets (set number -> physical parts list) from Rebrickable's CSV downloads.

Writes ONE compact file, ldraw_tools/data/set-catalog.bin (format: see ldraw_tools/sets.py). Per set it
stores the Rebrickable name, year, theme path and piece count, plus the parts list as (LDraw part, LDraw
colour, qty) after the Rebrickable -> LDraw mapping of ldraw_tools/inventory.py (the same maps, the same
translation logic as `inventory import`). The list is the full physical bill of the set: spare parts are
excluded, minifigs (inventory_minifigs) and sub-sets (inventory_sets) are expanded. Per set the builder also
keeps how many pieces were dropped because the part or the colour has no LDraw equivalent, and which
Rebrickable inventory version was used (the highest version of the set).

Rebrickable terms (read 2026-10-21 on https://rebrickable.com/downloads/): "Free for personal and commercial
use, provided you acknowledge Rebrickable as your data source. [...] You may automate downloading these
archives at most once a day." Hence THIS SCRIPT NEVER DOWNLOADS ANYTHING; it only reads files you already
fetched. See docs/plans/set-catalog.md and ATTRIBUTION.md.

Inputs (download by hand, at most once a day, from https://rebrickable.com/downloads/ : either the all-in-one
ZIP/TAR.GZ, or the per-file https://cdn.rebrickable.com/media/downloads/<name>.csv.gz links; gunzip into one folder):

    sets.csv  inventories.csv  inventory_parts.csv  inventory_sets.csv  inventory_minifigs.csv  themes.csv

plus the unpacked LDraw library (the folder containing parts/), so that parts without a parts/<id>.dat file
count as unmapped exactly as `inventory import` counts them with the library loaded.

Usage:
    python3 tools/build_set_catalog.py RB_DIR LDRAW_DIR [--out FILE] [--source-date YYYY-MM-DD] [--check]

--check writes nothing and exits 1 when the committed catalog differs (decoded content, not bytes: zlib output
may vary between zlib builds). Pure standard library, deterministic, works offline.

Dropped as noise (identified from themes.csv alone): sets whose root theme is Gear or Books (merchandise,
not buildable sets). Sets without any non-spare piece in their inventory are dropped too. Everything else is
kept, including Duplo, Modulex and Technic Mindstorms-style sets: their unmapped share is recorded per set.
"""
import argparse
import csv
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from ldraw_tools import inventory, sets  # noqa: E402

NOISE_ROOT_THEMES = ("Gear", "Books")
INPUTS = ("sets", "inventories", "inventory_parts", "inventory_sets", "inventory_minifigs", "themes")


def rows(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        yield from csv.DictReader(fh)


def theme_paths(rb_dir):
    themes = {r["id"]: r for r in rows(rb_dir / "themes.csv")}

    def path(tid):
        names, seen = [], set()
        while tid and tid in themes and tid not in seen:
            seen.add(tid)
            names.append(themes[tid]["name"])
            tid = themes[tid]["parent_id"]
        return " > ".join(reversed(names))
    return {tid: path(tid) for tid in themes}


def best_inventories(rb_dir):
    """set_num or fig_num -> (version, inventory id), highest version wins."""
    best = {}
    for r in rows(rb_dir / "inventories.csv"):
        version = int(r["version"])
        key = r["set_num"]
        if key not in best or version > best[key][0]:
            best[key] = (version, r["id"])
    return best


def read_inventory_parts(rb_dir, wanted):
    """inventory id -> Counter {(rb_part, rb_colour): qty}, spare rows skipped."""
    parts = defaultdict(Counter)
    for r in rows(rb_dir / "inventory_parts.csv"):
        if r["inventory_id"] in wanted and r["is_spare"].strip().lower() not in ("true", "t", "1"):
            parts[r["inventory_id"]][(r["part_num"], int(r["color_id"]))] += int(r["quantity"])
    return parts


class Library:
    """The only thing inventory._ldraw_part asks of a library: by_code membership."""

    def __init__(self, codes):
        self.by_code = codes


def library_codes(ldraw_dir):
    parts = Path(ldraw_dir) / "parts"
    if not parts.is_dir():
        raise SystemExit(f"{ldraw_dir} has no parts/ folder; pass the unpacked LDraw library root")
    return frozenset(p.stem.casefold() for p in parts.glob("*.dat"))


def compute(rb_dir, ldraw_dir):
    rb_dir = Path(rb_dir)
    missing = [n for n in INPUTS if not (rb_dir / f"{n}.csv").is_file()]
    if missing:
        raise SystemExit(f"{rb_dir} lacks " + ", ".join(f"{n}.csv" for n in missing) + " (download them by hand first; this script never downloads)")
    maps = inventory.load_maps()
    library = Library(library_codes(ldraw_dir)) if ldraw_dir else None
    themes = theme_paths(rb_dir)
    all_sets = list(rows(rb_dir / "sets.csv"))
    dropped = Counter()
    keep = {}
    for r in all_sets:
        path = themes.get(r["theme_id"], "")
        if path.split(" > ")[0] in NOISE_ROOT_THEMES:
            dropped["noise theme " + path.split(" > ")[0]] += 1
        else:
            keep[r["set_num"]] = r
    best = best_inventories(rb_dir)
    # inventories needed: those of kept sets, plus everything reachable through minifigs and sub-sets
    mini_all = defaultdict(list)
    sub_all = defaultdict(list)
    chosen = {best[s][1] for s in keep if s in best}
    for name, key, store in (("inventory_minifigs", "fig_num", mini_all), ("inventory_sets", "set_num", sub_all)):
        for r in rows(rb_dir / f"{name}.csv"):
            store[r["inventory_id"]].append((r[key], int(r["quantity"])))
    frontier = list(chosen)
    while frontier:
        inv_id = frontier.pop()
        for child, _ in mini_all.get(inv_id, []) + sub_all.get(inv_id, []):
            if child in best and best[child][1] not in chosen:
                chosen.add(best[child][1])
                frontier.append(best[child][1])
    own = read_inventory_parts(rb_dir, chosen)
    memo, active = {}, set()

    def raw_bom(key):
        """Full physical bill {(rb_part, rb_colour): qty} of a set or minifig, expanded through minifigs and sub-sets."""
        if key in memo:
            return memo[key]
        if key not in best or key in active:  # no inventory, or a reference cycle: contributes nothing
            return Counter()
        active.add(key)
        inv_id = best[key][1]
        total = Counter(own.get(inv_id, {}))
        for child, qty in mini_all.get(inv_id, []) + sub_all.get(inv_id, []):
            for line, q in raw_bom(child).items():
                total[line] += q * qty
        active.discard(key)
        memo[key] = total
        return total

    translate = {}

    def mapped(rb_part, rb_colour):
        """(ldraw part, ldraw colour) or the reason it is dropped, mirroring inventory.parse_rebrickable_csv."""
        k = (rb_part, rb_colour)
        if k not in translate:
            part = inventory._ldraw_part(rb_part, maps, library)
            entry = maps["colours"].get(rb_colour)
            if part is None:
                translate[k] = "part"
            elif entry is None or rb_colour in maps["unmapped_colours"]:
                translate[k] = "colour"
            else:
                translate[k] = (part, int(entry["ldraw"]))
        return translate[k]

    result = []
    for set_num in sorted(keep):
        r = keep[set_num]
        raw = raw_bom(set_num)
        if not raw:
            dropped["no inventory or only spares"] += 1
            continue
        lines, drop = Counter(), Counter()
        for (rb_part, rb_colour), qty in raw.items():
            m = mapped(rb_part, rb_colour)
            if isinstance(m, str):
                drop[m] += qty
            else:
                lines[m] += qty
        result.append(dict(set_num=set_num, name=r["name"], year=int(r["year"] or 0), theme=themes.get(r["theme_id"], ""),
                           num_parts=int(r["num_parts"] or 0), inv_version=best[set_num][0],
                           mapped_pieces=sum(lines.values()), dropped_part=drop["part"], dropped_colour=drop["colour"],
                           lines=sorted((p, c, q) for (p, c), q in lines.items())))
    return result, dict(dropped), len(all_sets)


def semantic(path):
    cat = sets.read_catalog(path)
    return cat.meta, [dict(s, lines=cat.lines(s["set_num"])) for s in cat.sets]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("rb_dir", help="folder with the unzipped Rebrickable CSV files")
    ap.add_argument("ldraw_dir", help="unpacked LDraw library root (contains parts/)")
    ap.add_argument("--out", type=Path, default=sets.CATALOG_FILE)
    ap.add_argument("--source-date", default="", help="date the Rebrickable files were downloaded, stored in the catalog")
    ap.add_argument("--check", action="store_true", help="do not write; exit 1 if the catalog would change")
    a = ap.parse_args(argv)
    start = time.time()
    result, dropped, total = compute(a.rb_dir, a.ldraw_dir)
    meta = dict(source="Rebrickable database downloads (https://rebrickable.com/downloads/)", source_date=a.source_date,
                sets_in_source=total, dropped=dropped, maps="ldraw_tools/data/rebrickable-colours.json + rebrickable-parts.json",
                ldraw_parts_checked=bool(a.ldraw_dir))
    if a.check:
        want_meta, want = meta, result
        old_meta, old = semantic(a.out) if a.out.is_file() else (None, None)
        same = old_meta == want_meta and old == want
        print("catalog up to date" if same else f"{a.out} differs from what these inputs generate")
        return 0 if same else 1
    size = sets.write_catalog(a.out, meta, result)
    print(f"wrote {a.out} ({size:,} bytes, {len(result)} sets of {total} in sources) in {time.time() - start:.1f}s")
    for reason, n in sorted(dropped.items()):
        print(f"  dropped {n:>6} sets: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
