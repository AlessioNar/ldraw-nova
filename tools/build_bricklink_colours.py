#!/usr/bin/env python3
"""Build the BrickLink -> LDraw colour table used by the BrickLink/BrickStore importers and the exports.

Writes ldraw_tools/data/bricklink-colours.json (sorted keys, trailing newline):

* colours   BrickLink colour id -> {name, ldraw, exact, method}
* by_name   normalised BrickLink colour name -> BrickLink colour id (BrickLink CSV exports carry names, not ids)
* reverse   LDraw colour code -> BrickLink colour id (for the wanted-list export; one id per code)
* unmapped  BrickLink colour ids with no LDraw colour (listed, never guessed)

Inputs (not committed):

* A CSV with columns bl_id,bl_name,ldraw_id,ldraw_name: the `legoCols` table of the CRAN package `legocolors`
  (MIT, https://cran.r-project.org/package=legocolors), exported with `write.csv(legoCols[c("bl_id","bl_name","ldraw_id",
  "ldraw_name")], row.names = FALSE)`. It pairs BrickLink and LDraw colours that LEGO and BrickLink both document.
* ldraw_tools/data/rebrickable-colours.json from this repo, as a second opinion by colour name.

Method per BrickLink colour: a single LDraw code listed for it is taken as is; several codes (the source lists a colour twice) are
resolved by the normalised name of the LDraw colour; with no code, a normalised-name match against the Rebrickable colour table
(Rebrickable names follow BrickLink's) is used and keeps the Rebrickable `exact` flag; otherwise the colour is unmapped.

Usage:
    python3 tools/build_bricklink_colours.py LEGOCOLORS.csv [--out-dir DIR] [--check]
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "ldraw_tools" / "data"
OUT_FILE = "bricklink-colours.json"
RB_FILE = "rebrickable-colours.json"
SOURCE = "CRAN package legocolors 0.4.0 (MIT, Matthew Leonawicz) BrickLink/LDraw pairs, completed by Rebrickable colour names; see docs/plans/rebrickable-mapping.md"


def norm_name(s):
    s = s.lower().replace("gray", "grey").replace("lt ", "light ")
    return re.sub(r"[^a-z0-9]", "", s)


def load_source(path):
    by_id = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                bl_id = int(row["bl_id"])
            except ValueError:
                continue
            entry = by_id.setdefault(bl_id, {"name": row["bl_name"].strip(), "codes": {}})
            if row["ldraw_id"].strip() not in ("", "NA"):
                entry["codes"][int(float(row["ldraw_id"]))] = row["ldraw_name"].strip()
    return by_id


def build(source, rb):
    rb_by_name = {}
    for rid in sorted(rb, key=int):
        entry = rb[rid]
        rb_by_name.setdefault(norm_name(entry["name"]), entry)
    colours, unmapped = {}, []
    for bl_id in sorted(source):
        name, codes = source[bl_id]["name"], source[bl_id]["codes"]
        key = norm_name(name)
        if len(codes) == 1:
            colours[bl_id] = dict(name=name, ldraw=next(iter(codes)), exact=True, method="legocolors")
        elif len(codes) > 1:
            named = [c for c, n in codes.items() if norm_name(n) == key]
            if len(named) == 1:
                colours[bl_id] = dict(name=name, ldraw=named[0], exact=True, method="legocolors-name")
        if bl_id not in colours and key in rb_by_name:
            entry = rb_by_name[key]
            colours[bl_id] = dict(name=name, ldraw=int(entry["ldraw"]), exact=bool(entry.get("exact", True)), method="rebrickable-name")
        if bl_id not in colours:
            unmapped.append(bl_id)
    reverse = {}
    for bl_id, entry in sorted(colours.items()):    # one BrickLink id per LDraw code: the one whose name matches, else the lowest id
        if not entry["exact"]:
            continue
        current = reverse.get(entry["ldraw"])
        better = current is None or (norm_name(entry["name"]) == norm_name(source[bl_id]["codes"].get(entry["ldraw"], ""))
                                     and norm_name(colours[current]["name"]) != norm_name(source[current]["codes"].get(entry["ldraw"], "")))
        if better:
            reverse[entry["ldraw"]] = bl_id
    by_name = {}
    for bl_id, entry in sorted(colours.items()):
        by_name.setdefault(norm_name(entry["name"]), bl_id)
    return dict(source=SOURCE, version=1, colours={str(k): v for k, v in colours.items()}, by_name=by_name,
                reverse={str(k): v for k, v in sorted(reverse.items())}, unmapped=[str(u) for u in unmapped])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("legocolors", type=Path, help="CSV with bl_id,bl_name,ldraw_id,ldraw_name")
    ap.add_argument("--out-dir", type=Path, default=DATA)
    ap.add_argument("--check", action="store_true", help="do not write; exit 1 if the file would change")
    args = ap.parse_args(argv)
    rb = json.loads((DATA / RB_FILE).read_text(encoding="utf-8-sig"))["colours"]
    data = build(load_source(args.legocolors), rb)
    text = json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    path = args.out_dir / OUT_FILE
    print(f"{len(data['colours'])} BrickLink colours mapped, {len(data['unmapped'])} unmapped, {len(data['reverse'])} reverse entries")
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            print(f"{path} differs from the generated data", file=sys.stderr)
            return 1
        return 0
    path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
