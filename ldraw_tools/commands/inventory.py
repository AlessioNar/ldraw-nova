"""User parts inventories: import a Rebrickable CSV, then list, show, search and check models against it."""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from .. import buildable
from ..common import atomic_write, dumps, get_parts
from .. import importers
from ..inventory import Inventory, available_inventory, ParseError, check_model, make_inventory, search_owned, slugify, summary
from .base import Command, positive


def fraction(value):
    value = float(value)
    if not 0 <= value <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return value


def store_dir(value=None):
    return Path(value or os.environ.get("LDRAW_NOVA_INVENTORY_DIR") or "inventories").expanduser()


def load_inventory(store, name):
    for candidate in (slugify(name), name):
        path = store / f"{candidate}.json"
        if path.is_file():
            return Inventory.from_json(json.loads(path.read_text(encoding="utf-8")))
    for path in sorted(store.glob("[!.]*.json")) if store.is_dir() else []:
        inv = Inventory.from_json(json.loads(path.read_text(encoding="utf-8")))
        if inv.name.casefold() == name.casefold():
            return inv
    raise ValueError(f"No inventory named {name!r} in {store}")


def optional_library():
    """Part descriptions and colour names are nice-to-have; fall back to the mapping data without a library."""
    try:
        return get_parts()
    except ValueError:
        return None


def register(commands):
    c = commands.add_parser("inventory", help="Import and query your own parts inventory (Rebrickable CSV)")
    sub = c.add_subparsers(dest="inventory_command", required=True)

    def common(p, store=True):
        if store:
            p.add_argument("--store", help="Inventory directory (default $LDRAW_NOVA_INVENTORY_DIR, then ./inventories)")
        p.add_argument("--json", action="store_true", help="Print a JSON report instead of text")

    p = sub.add_parser("import", help="Parse a Rebrickable CSV and store it")
    p.add_argument("file")
    p.add_argument("--name", help="Inventory name (default: the file name)")
    p.add_argument("--include-spares", action="store_true", help="Count spare parts as owned")
    p.add_argument("--force", action="store_true", help="Replace an existing inventory with the same name")
    p.add_argument("--format", choices=sorted(importers.FORMATS), help="Read the file as this format instead of detecting it")
    p.add_argument("--map", action="append", default=[], metavar="ROLE=VALUE",
                   help="Generic CSV: part=HEADER, colour=HEADER, qty=HEADER, system=bricklink|rebrickable|ldraw|name (repeatable)")
    p.add_argument("--dry-run", action="store_true", help="Parse and report only; write nothing (the web app previews imports with this)")
    p.add_argument("--family-colour", action="append", default=[], metavar="FAMILY=CODE",
                   help="Pileometer: LDraw colour for a colour family, e.g. Grays=72 (repeatable)")
    common(p)
    common(sub.add_parser("list", help="List stored inventories"))
    p = sub.add_parser("show", help="Summarise one inventory")
    p.add_argument("name")
    p.add_argument("--limit", type=positive, default=20)
    common(p)
    p = sub.add_parser("search", help="Find owned parts by number or description")
    p.add_argument("name", help="Inventory name, or several separated by commas")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--colour", type=int, help="LDraw colour code")
    p.add_argument("--limit", type=positive, default=50)
    common(p)
    p = sub.add_parser("check", help="Compare a model's physical BOM with inventories")
    p.add_argument("model")
    p.add_argument("name", help="Inventory name, or several separated by commas")
    p.add_argument("--mode", choices=["prefer", "only"], default="prefer", help="only: exit 1 when the inventory is short")
    p.add_argument("--report", help="Also write the JSON report to this file (--json prints it; stdout may be truncated for big models)")
    common(p)
    p = sub.add_parser("buildable", help="Rank the bundled set models by how much of each the inventories cover")
    p.add_argument("name", nargs="?", help="Inventory name, or several separated by commas")
    p.add_argument("--limit", type=positive, default=20)
    p.add_argument("--min-parts", type=int, default=buildable.MIN_PARTS, help="Skip models with fewer pieces (default %(default)s)")
    p.add_argument("--max-parts", type=positive, help="Skip models with more pieces")
    missing = p.add_mutually_exclusive_group()
    missing.add_argument("--max-missing", type=int, help="Only models missing at most this many pieces")
    missing.add_argument("--max-missing-share", type=fraction, help="Only models missing at most this share (0-1) of their pieces")
    p.add_argument("--query", default="", help="Only models whose file name or description has all these words")
    p.add_argument("--substitute-colours", action="store_true",
                   help="Also report the completeness if any owned colour of a part may stand in for a missing one (ranking stays exact)")
    p.add_argument("--available", action="store_true",
                   help="Rank what is free: owned minus built sets and reservations (reads .collection-reservations.json in --store)")
    p.add_argument("--keep-subvariants", action="store_true", help="Do not drop sub-variants that fit inside their parent set model")
    p.add_argument("--report", help="Also write the JSON report to this file")
    p.add_argument("--warm", action="store_true", help="Only build the BOM cache (no inventory needed)")
    p.add_argument("--cache-only", action="store_true", help="Rank what is cached; never build BOMs (the report says how many are pending)")
    p.add_argument("--max-build-seconds", type=positive, default=buildable.DEFAULT_BUDGET,
                   help="Stop building BOMs after this long; rerun to resume (default %(default)s)")
    p.add_argument("--workers", type=positive, help="BOM build processes (default min(4, CPUs))")
    p.add_argument("--cache-dir", help="BOM cache directory (default .cache/buildable/<library signature>)")
    p.add_argument("--models-dir", help=argparse.SUPPRESS)      # tests: a small model folder instead of the bundled catalogue
    common(p)
    p = sub.add_parser("remove", help="Delete a stored inventory")
    p.add_argument("name")
    common(p)


def needs_parts(args):
    return args.inventory_command in {"check", "buildable"}


def lines_import(inv, result, path):
    out = [f"Imported {inv.name!r} ({inv.id}) from {path}",
           f"  dialect: {result.dialect}",
           f"  rows read: {result.total_rows}   pieces counted: {result.total_pieces}   spare pieces: {result.spare_pieces}",
           f"  mapped: {inv.total_pieces} pieces, {inv.distinct_parts} distinct parts, {inv.distinct_colours} distinct colours",
           f"  unmapped: {len(inv.unmapped_parts)} part line(s), {len(inv.unmapped_colours)} colour line(s), {inv.unmapped_pieces} pieces"]
    if inv.unmapped_parts:
        out.append("  most frequent unmapped parts (Rebrickable part, colour, qty):")
        out += [f"    {u['rb_part']}  colour {u['colour']}  x{u['qty']}" for u in inv.unmapped_parts[:10]]
    if inv.unmapped_colours:
        out.append("  most frequent unmapped colours (Rebrickable colour, part, qty):")
        out += [f"    colour {u['rb_colour']}  part {u['part']}  x{u['qty']}" for u in inv.unmapped_colours[:10]]
    out += [f"  warning: {w}" for w in result.warnings]
    return out


def lines_check(report):
    out = [report["summary"]]
    if report["short"]:
        out.append(f"{'part':<14}{'colour':<24}{'needed':>7}{'owned':>7}{'missing':>8}")
        out += [f"{s['part']:<14}{s['colour']:>3} {s['colour_name'][:19]:<20}{s['needed']:>7}{s['owned']:>7}{s['missing']:>8}" for s in report["short"]]
    for s in report["colour_substitutions"]:
        out.append(f"  {s['part']} (colour {s['wanted_colour']}) owned in: " + ", ".join(f"{o['colour']} x{o['qty']}" for o in s["owned_colours"][:6]))
    return out


def lines_buildable(report):
    out = [f"Inventories: {', '.join(report['inventories'])}: {report['inventory_pieces']} mapped pieces; "
           f"{report['unmapped_pieces']} pieces ({report['unmapped_share']:.0%}) could not be mapped to LDraw and count as not owned"]
    c = report["counts"]
    out.append(f"Models: {c['models']} scanned, {c['parsed']} parsed, {c['skipped']} skipped (unparsable), {c['too_small']} too small, "
               f"{c['subvariants']} sub-variants dropped, {c['ranked']} ranked")
    out += [f"warning: {w}" for w in report["warnings"]]
    for i, r in enumerate(report["results"], 1):
        sub = f"  with colour substitution {r['substituted_completeness']:.1%}" if "substituted_completeness" in r else ""
        if r.get("maybe_matched"):
            sub += f"  (+{r['maybe_matched']} maybe from colour-family pieces: {r['maybe_completeness']:.1%})"
        out.append(f"{i:>3}. {r['id']:<30}{r['completeness']:>7.1%}  {r['matched']}/{r['pieces']} pieces, {r['missing']} missing in {r['missing_lines']} line(s){sub}"
                   + (f"  ({r['custom_pieces']} on custom parts no inventory covers)" if r["custom_pieces"] else ""))
        if r["description"]:
            out.append(f"     {r['description'][:110]}")
        out += [f"     missing {m['part']} colour {m['colour']} {m['colour_name']}: need {m['needed']}, have {m['owned']}" for m in r["top_missing"]]
    return out or ["No models"]


def buildable_report(args, library, invs):
    scan = buildable.scan(library, root=args.models_dir, cache=args.cache_dir, build=not args.cache_only, budget=args.max_build_seconds,
                          workers=args.workers, progress=buildable.stderr_progress)
    base = dict(models=scan.total, cached=len(scan.entries), built=scan.built, pending=len(scan.pending), partial=bool(scan.pending),
                skipped=[dict(file=e.name, error=e.error) for e in scan.failed])
    if args.warm:
        return dict(base, warm=True, results=[], inventories=[], warnings=[])
    results, counts = buildable.rank(scan.entries, invs, library=library, descriptions=buildable.descriptions(), limit=args.limit,
                                     min_parts=args.min_parts, max_parts=args.max_parts, max_missing=args.max_missing,
                                     max_missing_share=args.max_missing_share, query=args.query,
                                     substitute_colours=args.substitute_colours, keep_subvariants=args.keep_subvariants)
    review_pieces = sum(r["qty"] for i in invs for r in i.review)
    mapped, unmapped = sum(i.total_pieces for i in invs), sum(i.unmapped_pieces for i in invs)
    warnings = []
    if scan.pending:
        warnings.append(f"BOM cache is incomplete: {len(scan.pending)} of {scan.total} models not scanned yet; rerun to continue")
    if counts["skipped"]:
        warnings.append(f"{counts['skipped']} model file(s) could not be parsed and were skipped")
    if review_pieces:
        warnings.append(f"{review_pieces} colour-family pieces are counted separately as maybe, never in the exact numbers")
    if unmapped:
        warnings.append("pieces that could not be mapped to LDraw parts or colours count as not owned, so completeness is understated")
    return dict(base, inventories=[i.name for i in invs], inventory_pieces=mapped, maybe_pieces=review_pieces, unmapped_pieces=unmapped,
                unmapped_share=round(unmapped / (mapped + unmapped), 4) if mapped + unmapped else 0.0, counts=counts,
                substitute_colours=args.substitute_colours, subvariants_dropped=not args.keep_subvariants, warnings=warnings, results=results)


def run(args, library, parts):
    action = args.inventory_command
    store = store_dir(args.store)
    lib = parts if parts is not None else (optional_library() if action in {"show", "search"} else None)

    def finish(report, text, status=0):
        if args.json:
            return report, status
        print("\n".join(text))
        return None, status

    if action == "import":
        path = Path(args.file)
        try:
            text = path.read_bytes().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ParseError(f"{path.name} is not UTF-8 text: {exc}") from exc
        family_colours = {}
        for item in args.family_colour:
            family, _, code = item.partition("=")
            if not code.strip().isdigit():
                raise ValueError(f"--family-colour expects FAMILY=CODE, got {item!r}")
            family_colours[family.strip().title()] = int(code)
        mapping = {}
        for item in args.map:
            role, _, value = item.partition("=")
            if role.strip() not in {"part", "colour", "qty", "system"} or not value:
                raise ValueError(f"--map expects part=, colour=, qty= or system= followed by a value, got {item!r}")
            mapping[role.strip()] = value
        try:
            result = importers.parse(text, include_spares=args.include_spares, library=optional_library(), family_colours=family_colours,
                                     filename=path.name, format=args.format, mapping=mapping or None)
        except importers.NeedsMapping as exc:
            return dict(checks_passed=False, error=str(exc), error_type="NeedsMapping", mapping_needed=exc.to_json()), 2
        name = args.name or path.stem
        inv = make_inventory(name, result, source_filename=path.name)
        fmt = (result.origin or {}).get("format", "")
        report = dict(inventory=inv.to_json(), dialect=result.dialect, format=fmt, total_rows=result.total_rows,
                      total_pieces=result.total_pieces, spare_pieces=result.spare_pieces, warnings=result.warnings, stored="")
        if (result.origin or {}).get("mapping"):
            report.update(mapping=result.origin["mapping"], header_signature=result.origin["header_signature"])
        if args.dry_run:
            if result.pileometer:
                report["pileometer"] = result.pileometer
            return finish(report, lines_import(inv, result, path) + ["  dry run: nothing stored"])
        if (store / f"{inv.id}.json").exists() and not args.force:
            raise ValueError(f"Inventory {inv.id!r} already exists in {store}; use --force or another --name")
        atomic_write(store / f"{inv.id}.json", json.dumps(inv.to_json(), indent=2) + "\n")
        shutil.copyfile(path, store / f"{inv.id}.csv")
        report["stored"] = str(store / f"{inv.id}.json")
        if result.pileometer:
            report["pileometer"] = result.pileometer
        return finish(report, lines_import(inv, result, path) + [f"  stored: {store / (inv.id + '.json')}"])
    if action == "list":
        invs = [Inventory.from_json(json.loads(p.read_text(encoding="utf-8"))) for p in sorted(store.glob("[!.]*.json"))] if store.is_dir() else []
        report = dict(store=str(store), inventories=[dict(id=i.id, name=i.name, pieces=i.total_pieces, distinct_parts=i.distinct_parts,
                                                        source_filename=i.source_filename, created_at=i.created_at) for i in invs])
        return finish(report, [f"{i['id']:<24}{i['pieces']:>8} pieces {i['distinct_parts']:>6} parts  {i['source_filename']}" for i in report["inventories"]]
                      or [f"No inventories in {store}"])
    if action == "remove":
        inv = load_inventory(store, args.name)
        for ext in ("json", "csv"):
            (store / f"{inv.id}.{ext}").unlink(missing_ok=True)
        return finish(dict(removed=inv.id), [f"Removed {inv.id}"])
    if action == "buildable" and args.warm:
        return finish(buildable_report(args, parts, []), ["BOM cache is up to date"])
    if not args.name:
        raise ValueError("Name at least one inventory")
    invs = [load_inventory(store, n.strip()) for n in args.name.split(",") if n.strip()]
    if action == "buildable":
        if args.available:
            invs = [available_inventory(invs, store / ".collection-reservations.json")]
        report = buildable_report(args, parts, invs)
        if args.report:
            atomic_write(args.report, dumps(report) + "\n")
        return finish(report, lines_buildable(report))
    if action == "show":
        data = summary(invs, args.limit, lib)
        data["unmapped_parts"] = sum(len(i.unmapped_parts) for i in invs)
        data["warnings"] = [w for i in invs for w in i.warnings]
        text = [f"{', '.join(i.name for i in invs)}: {data['total_pieces']} pieces, {data['distinct_parts']} parts, {data['distinct_colours']} colours",
                "top parts:"] + [f"  {p['part']:<12}{p['qty']:>6}  {p['description']}" for p in data["top_parts"]] + \
               ["top colours:"] + [f"  {c['colour']:>4} {c['name']:<24}{c['qty']:>6}" for c in data["top_colours"]] + \
               [f"warning: {w}" for w in data["warnings"]]
        return finish(data, text)
    if action == "search":
        hits = search_owned(invs, args.query, colour=args.colour, limit=args.limit, library=lib)
        return finish(dict(matches=hits), [f"{h['part']:<12}{h['colour']:>4} {h['colour_name']:<22}{h['qty']:>6}  {h['description']}" for h in hits] or ["No matches"])
    if action == "check":
        from ..validation import validate_file
        model, diagnostics = validate_file(args.model, parts, assembly=True)
        if model is None or any(d["severity"] == "error" for d in diagnostics):
            raise ValueError("Model is invalid: " + "; ".join(d["message"] for d in diagnostics if d["severity"] == "error")[:400])
        report = check_model(model, invs, mode=args.mode, library=parts)
        status = 1 if args.mode == "only" and not report["complete"] else 0
        return finish(report, lines_check(report), status)
    raise ValueError(f"Unknown inventory command {action}")


COMMANDS = {"inventory": Command(register, run, needs_parts)}
