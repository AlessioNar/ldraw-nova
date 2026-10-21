"""User parts inventories: import a Rebrickable CSV, then list, show, search and check models against it."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from ..common import atomic_write, get_parts
from ..inventory import (Inventory, ParseError, check_model, make_inventory, parse_rebrickable_csv,
                         search_owned, slugify, summary)
from .base import Command, positive


def store_dir(value=None):
    return Path(value or os.environ.get("LDRAW_NOVA_INVENTORY_DIR") or "inventories").expanduser()


def load_inventory(store, name):
    for candidate in (slugify(name), name):
        path = store / f"{candidate}.json"
        if path.is_file():
            return Inventory.from_json(json.loads(path.read_text(encoding="utf-8")))
    for path in sorted(store.glob("*.json")) if store.is_dir() else []:
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
    p = sub.add_parser("remove", help="Delete a stored inventory")
    p.add_argument("name")
    common(p)


def needs_parts(args):
    return args.inventory_command == "check"


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
        result = parse_rebrickable_csv(text, include_spares=args.include_spares, library=optional_library())
        name = args.name or path.stem
        inv = make_inventory(name, result, source_filename=path.name)
        if (store / f"{inv.id}.json").exists() and not args.force:
            raise ValueError(f"Inventory {inv.id!r} already exists in {store}; use --force or another --name")
        atomic_write(store / f"{inv.id}.json", json.dumps(inv.to_json(), indent=2) + "\n")
        shutil.copyfile(path, store / f"{inv.id}.csv")
        report = dict(inventory=inv.to_json(), dialect=result.dialect, total_rows=result.total_rows, total_pieces=result.total_pieces,
                      spare_pieces=result.spare_pieces, warnings=result.warnings, stored=str(store / f"{inv.id}.json"))
        return finish(report, lines_import(inv, result, path) + [f"  stored: {store / (inv.id + '.json')}"])
    if action == "list":
        invs = [Inventory.from_json(json.loads(p.read_text(encoding="utf-8"))) for p in sorted(store.glob("*.json"))] if store.is_dir() else []
        report = dict(store=str(store), inventories=[dict(id=i.id, name=i.name, pieces=i.total_pieces, distinct_parts=i.distinct_parts,
                                                        source_filename=i.source_filename, created_at=i.created_at) for i in invs])
        return finish(report, [f"{i['id']:<24}{i['pieces']:>8} pieces {i['distinct_parts']:>6} parts  {i['source_filename']}" for i in report["inventories"]]
                      or [f"No inventories in {store}"])
    if action == "remove":
        inv = load_inventory(store, args.name)
        for ext in ("json", "csv"):
            (store / f"{inv.id}.{ext}").unlink(missing_ok=True)
        return finish(dict(removed=inv.id), [f"Removed {inv.id}"])
    invs = [load_inventory(store, n.strip()) for n in args.name.split(",") if n.strip()]
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
