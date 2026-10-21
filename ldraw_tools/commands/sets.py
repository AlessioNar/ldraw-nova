"""Catalog of real LEGO sets (parts lists only): search, show, stats, and rank them against your inventories."""
from __future__ import annotations

import time

from .. import sets as catalog
from ..inventory import colour_name, combine, describe
from .base import Command, positive
from .inventory import load_inventory, optional_library, store_dir


def register(commands):
    c = commands.add_parser("sets", help="Search the offline catalog of real LEGO sets and find which ones your inventory can build")
    sub = c.add_subparsers(dest="sets_command", required=True)

    def common(p):
        p.add_argument("--json", action="store_true", help="Print a JSON report instead of text")

    def filters(p):
        p.add_argument("--theme", help="Theme name substring, e.g. Technic or 'Technic > Racers'")
        p.add_argument("--min-parts", type=int, help="Minimum Rebrickable piece count")
        p.add_argument("--max-parts", type=int, help="Maximum Rebrickable piece count")

    p = sub.add_parser("search", help="Find sets by number, name or theme (all words must match)")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--year", type=int)
    p.add_argument("--limit", type=positive, default=25)
    filters(p)
    common(p)
    p = sub.add_parser("show", help="Show one set and its parts list")
    p.add_argument("set_num", help="e.g. 8448-1 (8448 means 8448-1)")
    p.add_argument("--limit", type=positive, default=30, help="Parts lines to print in text mode (--json prints all)")
    common(p)
    common(sub.add_parser("stats", help="Catalog size, mapping coverage and provenance"))
    p = sub.add_parser("buildable", help="Rank every catalog set by how much of its parts list your inventory covers")
    p.add_argument("name", help="Inventory name, or several separated by commas")
    p.add_argument("--store", help="Inventory directory (default $LDRAW_NOVA_INVENTORY_DIR, then ./inventories)")
    p.add_argument("--limit", type=positive, default=20)
    p.add_argument("--max-missing-share", type=float, help="Only sets missing at most this share of their pieces (0.1 = 90%% covered)")
    p.add_argument("--query", default="", help="Words that must all appear in set number, name or theme")
    filters(p)
    common(p)


def needs_parts(args):
    return False


def row_line(s):
    return f"{s['set_num']:<12}{s['year']:>5} {s['num_parts']:>6}  {s['name'][:44]:<45}{s['theme']}"


def unmapped_note(r):
    return f" (+{r['dropped_pieces']} unmapped)" if r["dropped_pieces"] else ""


def run(args, library, parts):
    action = args.sets_command

    def finish(report, text):
        if args.json:
            return report, 0
        print("\n".join(text))
        return None, 0

    if action == "search":
        found = catalog.search_sets(args.query, theme=args.theme, year=args.year, min_parts=args.min_parts, max_parts=args.max_parts,
                                    limit=args.limit)
        bundled = catalog.bundled_set_nums()
        rows = [dict(s, has_model=s["set_num"] in bundled) for s in found]
        return finish(dict(count=len(rows), sets=rows), [f"{r['set_num']:<12}{r['year']:>5} {r['num_parts']:>6}  {r['name'][:40]:<41}{'[model] ' if r['has_model'] else ''}{r['theme']}" for r in rows] or ["No matching sets"])
    if action == "show":
        found = catalog.get_set(args.set_num)
        if found is None:
            raise ValueError(f"No set {args.set_num!r} in the catalog")
        lib = optional_library()
        lines = [dict(part=p, colour=col, colour_name=colour_name(col, lib), qty=q, description=describe(p, lib))
                 for p, col, q in catalog.load_catalog().lines(found["set_num"])]
        has_model = found["set_num"] in catalog.bundled_set_nums()
        report = dict(found, has_model=has_model, distinct_lines=len(lines), parts=lines)
        text = [f"{found['set_num']}  {found['name']} ({found['year']})", f"  theme: {found['theme']}",
                f"  pieces: {found['num_parts']} listed by Rebrickable; {found['mapped_pieces']} mapped to LDraw, "
                f"{found['dropped_part']} dropped (part has no LDraw file), {found['dropped_colour']} dropped (colour unmapped)",
                f"  Rebrickable inventory version {found['inv_version']}; bundled LDraw model: {'yes' if has_model else 'no (parts list only)'}",
                f"{len(lines)} distinct part/colour lines:"]
        text += [f"  {l['part']:<12}{l['colour']:>4} {l['colour_name'][:22]:<23}{l['qty']:>5}  {l['description']}" for l in lines[:args.limit]]
        if len(lines) > args.limit:
            text.append(f"  ... {len(lines) - args.limit} more (--limit or --json)")
        return finish(report, text)
    if action == "stats":
        data = catalog.catalog_stats()
        data["bundled_models"] = len(catalog.bundled_set_nums() & set(catalog.load_catalog().by_num))
        text = [f"{data['sets']} sets ({data['first_year']}-{data['last_year']}), {data['distinct_parts']} distinct LDraw parts, {data['pieces']} pieces",
                f"  mapped to LDraw: {data['mapped_pieces']} ({data['mapped_share']:.1%}); dropped: {data['dropped_part_pieces']} (no LDraw part), "
                f"{data['dropped_colour_pieces']} (colour unmapped)",
                f"  sets with a bundled LDraw model: {data['bundled_models']}",
                f"  source: {data['meta'].get('source')} downloaded {data['meta'].get('source_date') or 'on an unrecorded date'}",
                "  Data from Rebrickable, rebrickable.com", "  largest root themes: " + ", ".join(f"{k} {v}" for k, v in list(data["root_themes"].items())[:10])]
        return finish(data, text)
    if action == "buildable":
        store = store_dir(args.store)
        invs = [load_inventory(store, n.strip()) for n in args.name.split(",") if n.strip()]
        started = time.perf_counter()
        cat = catalog.load_catalog()
        ranked = catalog.rank_sets(cat, combine(invs), limit=args.limit, min_parts=args.min_parts, max_parts=args.max_parts,
                                   max_missing_share=args.max_missing_share, theme=args.theme, query=args.query)
        seconds = time.perf_counter() - started
        report = dict(inventories=[i.name for i in invs], catalog_sets=len(cat), seconds=round(seconds, 2), sets=ranked)
        text = [f"{'set':<12}{'year':>5} {'pieces':>6} {'covered':>8} {'missing':>8}  name"]
        text += [f"{r['set_num']:<12}{r['year']:>5} {r['needed']:>6} {r['completeness']:>8.1%} {r['missing']:>8}  {r['name'][:40]}"
                 f"{' [model]' if r['has_model'] else ''}{unmapped_note(r)}"
                 for r in ranked] or ["No set shares a part with the inventory"]
        text.append(f"ranked {len(cat)} sets in {seconds:.2f}s; covered = share of the set's mapped pieces owned (check_model measure); [model] = bundled LDraw model")
        return finish(report, text)
    raise ValueError(f"Unknown sets command {action}")


COMMANDS = {"sets": Command(register, run, needs_parts)}
