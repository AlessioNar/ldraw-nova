#!/usr/bin/env python3
"""Build the Rebrickable -> LDraw mapping data used by the parts-inventory feature.

Writes two deterministic JSON files (sorted keys, trailing newline):

* ldraw_tools/data/rebrickable-colours.json   Rebrickable colour id -> LDraw colour code
* ldraw_tools/data/rebrickable-parts.json     Rebrickable part_num -> LDraw part number
                                              (only where the two differ)

Inputs (download once; they are NOT committed):

* colors.csv, parts.csv, part_relationships.csv from the Rebrickable bulk downloads
    https://cdn.rebrickable.com/media/downloads/colors.csv.gz
    https://cdn.rebrickable.com/media/downloads/parts.csv.gz
    https://cdn.rebrickable.com/media/downloads/part_relationships.csv.gz
  (gunzip them into one folder).
* The complete official LDraw library (the folder that contains parts/ and
  LDConfig.ldr), from the "complete.zip" at https://library.ldraw.org/ .

Usage:
    python3 tools/build_rebrickable_maps.py RB_DIR LDRAW_DIR [--out-dir DIR] [--check]

RB_DIR holds the three CSV files; LDRAW_DIR is the unpacked "ldraw" folder.
Defaults write into ldraw_tools/data/ next to this repo; --check writes nothing and
exits 1 if the committed files differ from what would be generated. The coverage
report is printed to stdout. Pure standard library, works offline.

Colour method (the bulk colors.csv carries no LDraw ids): per Rebrickable colour,
first COLOUR_OVERRIDES, then a normalised-name match against LDConfig.ldr, then an
exact RGB match with the same transparency, then the nearest RGB (same transparency,
plain colour families only, within MAX_APPROX) flagged exact=false; otherwise unmapped. See docs/plans/rebrickable-mapping.md.

Part method: identity (same number exists as parts/<id>.dat) is the default and is
not listed. Otherwise follow Rebrickable relationships child -> parent for the
shape-preserving types P (print), T (pattern), M (mold), A (alternate) up to
MAX_HOPS, then strip LDraw print/pattern suffixes (prNNNN, patNNNN); an alias is
only emitted when the target file exists in the library.
"""
import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "ldraw_tools" / "data"
COLOURS_FILE = "rebrickable-colours.json"
PARTS_FILE = "rebrickable-parts.json"
SOURCE_COLOURS = ("Rebrickable colors.csv (bulk download) matched to the LDConfig.ldr of the official "
                  "LDraw library; see docs/plans/rebrickable-mapping.md")
SOURCE_PARTS = ("Rebrickable parts.csv and part_relationships.csv checked against parts/*.dat of the "
                "official LDraw library; see docs/plans/rebrickable-mapping.md")

# Relation types (child_part_num -> parent_part_num) whose parent has the same geometry.
SHAPE_RELS = ("P", "T", "M", "A")
MAX_HOPS = 4
SUFFIX_RE = re.compile(r"(pr|pat)\d+[a-z0-9]*$")
NO_LDRAW_CAP = 300

# Rebrickable colour id -> (LDraw code, exact). Hand-verified against LDConfig.ldr: same
# real LEGO colour although name and/or RGB differ (LDConfig re-valued many colours).
COLOUR_OVERRIDES = {
    "85": (85, True),    # Dark Purple = LDraw Medium_Lilac (#441A91), LEGO colour 268
    "89": (89, True),    # Royal Blue = LDraw 89 (named Blue_Violet in this LDConfig)
    "112": (112, True),  # Medium Bluish Violet = LDraw Medium_Violet
    "179": (179, True),  # Flat Silver (RGB 898788 identical); name match would hit 315
    "1103": (316, True), # Pearl Titanium = LDraw Titanium_Metallic (RGB 3E3C39 identical)
    "1062": (14, False), # Vibrant Yellow (#EBD800) has no LDraw code; nearest plain Yellow
}

# Rebrickable names that differ from LDConfig names only by abbreviation.
NAME_FIXES = {"Speckle DBGray-Silver": "Speckle Dark Bluish Gray-Silver"}

# Nearest-RGB matches farther than this (redmean distance, 0..765) are left unmapped.
MAX_APPROX = 40

# Colour families that are only chosen when the Rebrickable name carries the same word.
SPECIAL_WORDS = ("rubber", "modulex", "opal", "glitter", "speckle", "chrome", "pearl", "metallic",
                 "milky", "glow", "canvas", "electric", "hairy", "metal", "satin", "foil",
                 "magnet", "neon", "ink", "fluorescent")


def norm_name(s):
    s = s.lower().replace("gray", "grey").replace("lt ", "light ")
    return re.sub(r"[^a-z0-9]", "", s)


def parse_ldconfig(path):
    out = {}
    pat = re.compile(r"0 !COLOUR (\S+)\s+CODE\s+(\d+)\s+VALUE #([0-9A-Fa-f]{6})\s+EDGE\s+\S+(.*)")
    for line in path.read_text(encoding="utf8", errors="replace").splitlines():
        m = pat.match(line)
        if m:
            name = m.group(1)
            trans = "ALPHA" in m.group(4) or name.lower().startswith(("trans_", "opal_trans", "glitter_trans"))
            out[int(m.group(2))] = {"name": name, "rgb": m.group(3).upper(), "trans": trans}
    return out


def rgb_t(h):
    h = h.strip().upper()
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def dist(a, b):
    # redmean weighted distance: closer to perceived difference than plain euclid
    r = (a[0] + b[0]) / 2
    dr, dg, db = (a[0] - b[0]), (a[1] - b[1]), (a[2] - b[2])
    return ((2 + r / 256) * dr * dr + 4 * dg * dg + (2 + (255 - r) / 256) * db * db) ** 0.5


def family_ok(ld_name, rb_name):
    ln = set(re.split(r"[^a-z]+", ld_name.lower()))
    rn = set(re.split(r"[^a-z]+", rb_name.lower()))
    return all(w not in ln or w in rn for w in SPECIAL_WORDS)


def map_colour(row, ld):
    cid, name = row["id"], row["name"]
    trans = row["is_trans"].strip().lower() == "true"
    rgb = row["rgb"].strip().upper()
    if cid in COLOUR_OVERRIDES:
        code, exact = COLOUR_OVERRIDES[cid]
        if code not in ld:
            raise SystemExit(f"override {cid} -> {code} not in LDConfig")
        return code, "override", exact
    claimed = {c for c, ex in COLOUR_OVERRIDES.values() if ex}
    n = norm_name(NAME_FIXES.get(name, name))
    named = sorted(c for c, v in ld.items() if norm_name(v["name"]) == n and c not in claimed)
    if named:
        return named[0], "name", True
    pool = [c for c, v in ld.items() if v["trans"] == trans and family_ok(v["name"], name)]
    same = sorted(c for c in pool if ld[c]["rgb"] == rgb)
    if same:
        return same[0], "rgb", True
    if rgb and pool:
        t = rgb_t(rgb)
        best = min(sorted(pool), key=lambda c: dist(t, rgb_t(ld[c]["rgb"])))
        if dist(t, rgb_t(ld[best]["rgb"])) <= MAX_APPROX:
            return best, "rgb", False
    return None


def build_colours(rb_dir, ldraw_dir):
    ld = parse_ldconfig(ldraw_dir / "LDConfig.ldr")
    rows = list(csv.DictReader((rb_dir / "colors.csv").open(encoding="utf8")))
    colours, unmapped = {}, []
    for row in sorted(rows, key=lambda r: int(r["id"])):
        if row["id"] in ("-1", "9999"):  # [Unknown] / [No Color/Any Color]: not a real colour
            unmapped.append(row["id"])
            continue
        res = map_colour(row, ld)
        if res is None:
            unmapped.append(row["id"])
            continue
        code, method, exact = res
        colours[row["id"]] = {"ldraw": code, "name": row["name"], "method": method, "exact": exact}
    data = {"version": 1, "source": SOURCE_COLOURS,
            "colours": colours, "unmapped": unmapped}
    return data, rows, ld


def load_library(ldraw_dir):
    return {p.stem.lower() for p in (ldraw_dir / "parts").glob("*.dat")}


def build_parts(rb_dir, lib):
    parts = list(csv.DictReader((rb_dir / "parts.csv").open(encoding="utf8")))
    parents = defaultdict(list)
    for r in csv.DictReader((rb_dir / "part_relationships.csv").open(encoding="utf8")):
        if r["rel_type"] in SHAPE_RELS:
            parents[r["child_part_num"].lower()].append((r["rel_type"], r["parent_part_num"].lower()))

    def resolve(pn):
        frontier, seen = [pn], {pn}
        for _ in range(MAX_HOPS):
            nxt = []
            for cur in frontier:
                for _, par in sorted(parents.get(cur, ())):
                    if par in lib:
                        return par, "relation"
                    if par not in seen:
                        seen.add(par)
                        nxt.append(par)
            frontier = nxt
        for cur in sorted(seen):
            base = SUFFIX_RE.sub("", cur)
            if base != cur and base in lib:
                return base, "suffix"
        return None, None

    aliases, how, ident, unresolved = {}, {}, [], []
    for p in sorted(parts, key=lambda p: p["part_num"]):
        key, low = p["part_num"], p["part_num"].lower()
        if low in lib:
            ident.append(p)
            continue
        tgt, h = resolve(low)
        if tgt and tgt != low:
            aliases[key] = tgt
            how[key] = h
        else:
            unresolved.append(p)
    return parts, ident, aliases, how, unresolved, parents


def write_json(path, data, check):
    text = json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if check:
        return not path.exists() or path.read_text(encoding="utf8") != text
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf8")
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rb_dir", type=Path, help="folder with colors.csv, parts.csv, part_relationships.csv")
    ap.add_argument("ldraw_dir", type=Path, help="official LDraw library folder (contains parts/ and LDConfig.ldr)")
    ap.add_argument("--out-dir", type=Path, default=DATA)
    ap.add_argument("--check", action="store_true", help="do not write; exit 1 if files would change")
    a = ap.parse_args(argv)

    cdata, crows, ld = build_colours(a.rb_dir, a.ldraw_dir)
    lib = load_library(a.ldraw_dir)
    parts, ident, aliases, how, unresolved, parents = build_parts(a.rb_dir, lib)

    # parts that provably cannot be modelled: sticker sheets / paper / cloth etc. are not LDraw parts
    non_model = ("Cardboard/Paper", "Cloth", "Foam")
    no_ldraw = sorted(p["part_num"] for p in unresolved
                      if p["part_material"] in non_model or p["name"].startswith("Sticker Sheet"))
    cap_total = len(no_ldraw)
    no_ldraw = no_ldraw[:NO_LDRAW_CAP]
    notes = {
        "alias_methods": dict(sorted(Counter(how.values()).items())),
        "rules": "Aliases follow Rebrickable relation types P,T,M,A (child -> parent) up to "
                 f"{MAX_HOPS} hops, then strip prNNNN/patNNNN; target must exist as parts/<id>.dat. "
                 "Printed parts alias to their unprinted base (shape equivalent, print is lost).",
        "no_ldraw": f"first {NO_LDRAW_CAP} of {cap_total} unresolved Cardboard/Paper, Cloth, Foam or "
                    "sticker-sheet parts (LDraw does not model them)",
    }
    pdata = {"version": 1, "source": SOURCE_PARTS, "aliases": aliases, "notes": notes, "no_ldraw": no_ldraw}

    changed = write_json(a.out_dir / COLOURS_FILE, cdata, a.check)
    changed |= write_json(a.out_dir / PARTS_FILE, pdata, a.check)

    report(crows, cdata, ld, parts, ident, aliases, how, unresolved, parents)
    if a.check and changed:
        print("\nCHECK FAILED: committed files differ from regenerated output", file=sys.stderr)
        return 1
    return 0


def report(crows, cdata, ld, parts, ident, aliases, how, unresolved, parents):
    out = print
    cols = cdata["colours"]
    tot = sum(int(r["num_parts"]) for r in crows)
    out("== Colours ==")
    out(f"Rebrickable colours: {len(crows)}  mapped: {len(cols)}  unmapped: {len(cdata['unmapped'])} "
        f"({', '.join(cdata['unmapped'])})")
    by_method = Counter(v["method"] for v in cols.values())
    out("by method: " + ", ".join(f"{k}={by_method[k]}" for k in sorted(by_method)))
    for label, test in (("mapped", lambda v: True), ("exact", lambda v: v["exact"])):
        w = sum(int(r["num_parts"]) for r in crows if r["id"] in cols and test(cols[r["id"]]))
        n = sum(1 for v in cols.values() if test(v))
        out(f"{label}: {n} colours, {w}/{tot} part-weighted = {100 * w / tot:.2f}%")
    big = [r for r in crows if int(r["num_parts"]) >= 1000]
    out(f"colours with num_parts>=1000: {len(big)}, mapped {sum(1 for r in big if r['id'] in cols)}, "
        f"exact {sum(1 for r in big if r['id'] in cols and cols[r['id']]['exact'])}")
    out("approximate (exact=false), by num_parts:")
    rows = {r["id"]: r for r in crows}
    for cid, v in sorted(cols.items(), key=lambda kv: -int(rows[kv[0]]["num_parts"])):
        if not v["exact"]:
            out(f"  {cid:>5} {v['name']:<32} #{rows[cid]['rgb'].upper()} -> {v['ldraw']:>5} "
                f"{ld[v['ldraw']]['name']} #{ld[v['ldraw']]['rgb']}  ({rows[cid]['num_parts']} parts)")
    n = len(parts)
    out("\n== Parts ==")
    out(f"Rebrickable parts: {n}")
    out(f"resolved by identity: {len(ident)} ({100 * len(ident) / n:.1f}%)")
    for h, c in sorted(Counter(how.values()).items()):
        out(f"resolved by alias ({h}): {c}")
    res = len(ident) + len(aliases)
    out(f"resolved total: {res} ({100 * res / n:.1f}%)   unresolved: {len(unresolved)} ({100 * len(unresolved) / n:.1f}%)")
    mats = Counter(p["part_material"] for p in parts)
    for m in sorted(mats):
        tot_m = mats[m]
        un_m = sum(1 for p in unresolved if p["part_material"] == m)
        out(f"  material {m}: {tot_m} parts, unresolved {un_m}")
    out("unresolved by part_cat_id (top 12): " +
        ", ".join(f"{c}:{k}" for c, k in Counter(p["part_cat_id"] for p in unresolved).most_common(12)))
    kids = Counter()
    for lst in parents.values():
        for _, par in lst:
            kids[par] += 1
    ranked = sorted(unresolved, key=lambda p: (-kids.get(p["part_num"].lower(), 0), p["part_num"]))[:30]
    out("top 30 unresolved by number of Rebrickable variants (children) pointing at them:")
    for p in ranked:
        out(f"  {p['part_num']:<14} cat {p['part_cat_id']:>3}  children {kids.get(p['part_num'].lower(), 0):>3}  {p['name']}")


if __name__ == "__main__":
    sys.exit(main())
