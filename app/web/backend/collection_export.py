"""Exports of the parts collection: BrickLink wanted-list XML, Rebrickable CSV and a plain CSV for spreadsheets.

Pure translation of the owner's exact rows (`review` "maybe" pieces are never exported). The colour and part tables are the
toolkit's own data files, read as JSON like the rest of the backend does. A line whose colour has no id in the target system
is omitted and listed with the reason, never guessed, so the owner sees exactly what the file does not contain.
"""
from __future__ import annotations

import csv
import io
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import collection_store as store
import inventories
import part_info
import settings
from inventories import InventoryError

FORMATS = {
    "bricklink-xml": ("BrickLink wanted list (XML)", "xml", "application/xml"),
    "rebrickable-csv": ("Rebrickable CSV (Part, Color, Quantity)", "csv", "text/csv"),
    "csv": ("Plain CSV with names", "csv", "text/csv"),
}
MAX_LINES = 200_000
MAX_LISTED = 200            # omitted lines named in a response; the count is always complete
SOURCE_PREFIX = "source:"
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def data_dir() -> Path:
    for root in (settings.TOOLKIT_DIR, Path(__file__).resolve().parents[3]):
        if (Path(root) / "ldraw_tools/data/rebrickable-colours.json").is_file():
            return Path(root) / "ldraw_tools/data"
    return Path(settings.TOOLKIT_DIR) / "ldraw_tools/data"


@lru_cache(maxsize=4)
def _tables(directory: str) -> dict:
    def load(name):
        try:
            return json.loads((Path(directory) / name).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}
    rb = (load("rebrickable-colours.json").get("colours") or {})
    rank = {"override": 0, "name": 1, "rgb": 2}
    best: dict[int, tuple[tuple, int]] = {}
    for rid, entry in rb.items():
        if not entry.get("exact", True):
            continue
        code, rid = int(entry["ldraw"]), int(rid)
        key = (0 if rid == code else 1, rank.get(entry.get("method"), 3), rid)         # Rebrickable 4 is LDraw 4 Red
        if code not in best or key < best[code][0]:
            best[code] = (key, rid)
    bl = load("bricklink-colours.json")
    return {"rb": {code: rid for code, (_, rid) in best.items()},
            "bl": {int(code): int(bid) for code, bid in (bl.get("reverse") or {}).items()},
            "bl_missing": not bl, "rb_missing": not rb}


def clear_caches() -> None:
    _tables.cache_clear()


def _clean_text(value: str) -> str:
    return "'" + value if value.startswith(_FORMULA_START) else value


def lines_for(scope: str) -> tuple[list[dict], str]:
    """(lines, scope name). A line is {part, colour, qty, rb_part, rb_colour, note}: exact rows only, summed per part and colour."""
    if scope == "collection":
        ids, label = store.source_ids(), "collection"
    elif scope.startswith(SOURCE_PREFIX):
        sid = scope[len(SOURCE_PREFIX):]
        if not inventories.valid_id(sid) or not store.exists(sid):
            raise InventoryError(404, "no such inventory")
        ids, label = [sid], sid
    else:
        raise InventoryError(400, "scope must be 'collection' or 'source:<id>'")
    merged: dict[tuple[str, int], dict] = {}
    for sid in ids:
        try:
            data = store.load(sid)
        except InventoryError:
            continue
        if scope != "collection":
            label = str(data.get("name") or sid)
        for row in data.get("rows", []):
            qty = int(row.get("qty", 0))
            if qty <= 0:
                continue
            key = (str(row["part"]).casefold().removesuffix(".dat"), int(row["colour"]))
            line = merged.setdefault(key, {"part": key[0], "colour": key[1], "qty": 0, "rb_part": "", "rb_colour": 0, "note": ""})
            line["qty"] += qty
            line["rb_part"] = line["rb_part"] or str(row.get("rb_part") or "")
            line["rb_colour"] = line["rb_colour"] or int(row.get("rb_colour") or 0)
            line["note"] = line["note"] or str(row.get("note") or "")
    if len(merged) > MAX_LINES:
        raise InventoryError(413, "Too many lines to export at once; export one source at a time")
    return sorted(merged.values(), key=lambda l: (int((re.match(r"\d+", l["part"]) or ["999999999"])[0]), l["part"], l["colour"])), label


def clean_lines(lines: Any) -> list[dict]:
    """Caller-supplied lines (a shortage list) in the same shape: [{part, colour, qty}] with LDraw colour codes."""
    if not isinstance(lines, list) or not lines or len(lines) > MAX_LINES:
        raise InventoryError(400, "lines must be a non-empty list of {part, colour, qty}")
    merged: dict[tuple[str, int], int] = {}
    for item in lines:
        try:
            part, colour, qty = str(item["part"]).strip().casefold().removesuffix(".dat"), int(item["colour"]), int(item["qty"])
        except (KeyError, TypeError, ValueError):
            raise InventoryError(400, "Each line needs a part, an LDraw colour code and a quantity") from None
        if not re.fullmatch(r"[0-9a-z][0-9a-z_-]{0,39}", part) or colour < 0 or qty < 1 or qty > 1_000_000:
            raise InventoryError(400, f"Line {part!r} is not valid")
        merged[(part, colour)] = merged.get((part, colour), 0) + qty
    return [{"part": p, "colour": c, "qty": q, "rb_part": "", "rb_colour": 0, "note": ""} for (p, c), q in sorted(merged.items())]


def build(fmt: str, lines: list[dict], label: str = "collection") -> dict:
    """{format, filename, media_type, body, lines, pieces, omitted: [{part, colour, colour_name, qty, reason}], omitted_lines, notes}."""
    if fmt not in FORMATS:
        raise InventoryError(400, f"format must be one of {', '.join(FORMATS)}")
    tables = _tables(str(data_dir()))
    kept, omitted, notes = [], [], []
    system = {"bricklink-xml": "BrickLink", "rebrickable-csv": "Rebrickable"}.get(fmt)
    missing_table = {"BrickLink": tables["bl_missing"], "Rebrickable": tables["rb_missing"]}.get(system, False)
    if missing_table:
        raise InventoryError(503, f"The {system} colour table is not installed, so this export is unavailable")
    different_part = 0
    for line in lines:
        out = dict(line)
        if fmt == "bricklink-xml":
            colour = tables["bl"].get(line["colour"])
        elif fmt == "rebrickable-csv":
            colour = line["rb_colour"] or tables["rb"].get(line["colour"])
        else:
            colour = line["colour"]
        if colour is None:
            omitted.append({"part": line["part"], "colour": line["colour"], "colour_name": part_info.colour_name(line["colour"]),
                            "qty": line["qty"], "reason": f"no {system} colour for LDraw colour {line['colour']}"})
            continue
        # A print or mould variant that was read as its base part keeps the number it was imported with.
        part = (line["rb_part"] or line["part"]) if fmt != "csv" else line["part"]
        different_part += part != line["part"]
        out.update(export_part=part, export_colour=colour)
        kept.append(out)
    if different_part:
        notes.append(f"{different_part} line(s) use the part number they were imported with, because the LDraw number is a base part of a print or variant")
    if fmt == "bricklink-xml":
        body = "<INVENTORY>\n" + "".join(
            f"  <ITEM><ITEMTYPE>P</ITEMTYPE><ITEMID>{escape(l['export_part'])}</ITEMID><COLOR>{l['export_colour']}</COLOR><MINQTY>{l['qty']}</MINQTY></ITEM>\n"
            for l in kept) + "</INVENTORY>\n"
    else:
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        if fmt == "rebrickable-csv":
            writer.writerow(["Part", "Color", "Quantity"])
            for l in kept:
                writer.writerow([l["export_part"], l["export_colour"], l["qty"]])
        else:
            writer.writerow(["Part", "Description", "LDraw colour", "Colour", "Quantity", "Note"])
            for l in kept:
                writer.writerow([l["part"], _clean_text(part_info.describe(l["part"])), l["colour"], _clean_text(part_info.colour_name(l["colour"])), l["qty"], _clean_text(l["note"])])
        body = out.getvalue()
    title, ext, media = FORMATS[fmt]
    slug = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-") or "parts"
    return {"format": fmt, "title": title, "filename": f"{slug}-{fmt}.{ext}", "media_type": media, "body": body, "lines": len(kept),
            "pieces": sum(l["qty"] for l in kept), "omitted_lines": len(omitted), "omitted_pieces": sum(o["qty"] for o in omitted),
            "omitted": omitted[:MAX_LISTED], "notes": notes}


def export(fmt: str, scope: str) -> dict:
    lines, label = lines_for(scope)
    return build(fmt, lines, label)
