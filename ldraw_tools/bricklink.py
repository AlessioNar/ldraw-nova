"""BrickLink, BrickStore and generic CSV readers for the parts collection.

All of them reduce a file to ``(part, colour, qty)`` lines and share one mapper: the part number goes through the same
alias data as the Rebrickable import, the colour through ``data/bricklink-colours.json`` (BrickLink), the Rebrickable table
(Rebrickable ids and names) or is an LDraw code already. Nothing is guessed: a line whose part or colour has no mapping
lands in ``unmapped_parts`` / ``unmapped_colours`` with the reason, and is not counted as owned.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from functools import lru_cache
from pathlib import Path

from .inventory import MAX_BYTES, MAX_ROWS, DATA, ParseError, ParseResult, Row, _find, _lookup_part, _norm, load_maps

COLOURS_FILE = DATA / "bricklink-colours.json"
COLOUR_SYSTEMS = {"bricklink": "BrickLink colour ids or names", "rebrickable": "Rebrickable colour ids",
                  "ldraw": "LDraw colour codes", "name": "Colour names"}
ITEM_TYPES = {"P": "part", "M": "minifigure", "S": "set", "B": "book", "G": "gear", "I": "instruction", "O": "original box", "C": "catalogue"}

_ITEM_NO = {"itemno", "itemid", "itemnumber", "partno", "partnum", "partnumber", "partid", "part", "number", "item", "design", "designid"}
_COLOUR = {"color", "colour", "colorid", "colourid", "colorname", "colourname"}
_QTY_OWNED = ("qty", "quantity", "count", "amount", "pcs", "pieces")
_QTY_FILLED = ("qtyfilled", "filled")
_QTY_WANTED = ("minqty", "wantedqty", "wanted")
_BL_CSV_ITEM = {"itemno", "itemid", "itemnumber"}
_SPLIT_SYNONYMS = {"part": _ITEM_NO | {"brick", "element", "id"}, "colour": _COLOUR | {"col", "farbe"},
                   "qty": set(_QTY_OWNED) | {"owned", "have", "total", "qtyowned"}}


class NeedsMapping(ParseError):
    """A CSV no importer recognises: the caller must say which column is the part, the colour and the quantity."""

    def __init__(self, message, headers, sample, guess):
        super().__init__(message)
        self.headers, self.sample, self.guess = headers, sample, guess

    def to_json(self):
        return dict(headers=self.headers, sample=self.sample, guess=self.guess, signature=header_signature(self.headers))


# --------------------------------------------------------------------- colour tables

def _bl_file():
    override = os.environ.get("LDRAW_NOVA_RB_MAP_DIR")
    return Path(override) / COLOURS_FILE.name if override and (Path(override) / COLOURS_FILE.name).is_file() else COLOURS_FILE


@lru_cache(maxsize=4)
def _load_bl(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return dict(colours={}, by_name={}, warnings=[f"BrickLink colour table {Path(path).name} is missing or unreadable; BrickLink colours cannot be translated"])
    return dict(colours={int(k): v for k, v in (data.get("colours") or {}).items()},
                by_name={str(k): int(v) for k, v in (data.get("by_name") or {}).items()}, warnings=[])


def load_bricklink_colours():
    return _load_bl(str(_bl_file()))


def clear_cache():
    _load_bl.cache_clear()


def norm_colour_name(name):
    name = name.lower().replace("gray", "grey").replace("lt ", "light ")
    return re.sub(r"[^a-z0-9]", "", name)


def _rebrickable_names(maps):
    out = {}
    for rid in sorted(maps["colours"]):
        entry = maps["colours"][rid]
        if entry.get("name") and rid not in maps["unmapped_colours"]:
            out.setdefault(norm_colour_name(entry["name"]), (rid, entry))
    return out


class _Colours:
    """Resolves a colour cell of one source system to ``(ldraw code, exact, rb_colour)``, or None."""

    def __init__(self, system, maps):
        self.system, self.maps = system, maps
        self.bl = load_bricklink_colours() if system in ("bricklink", "name") else None
        self.rb_names = _rebrickable_names(maps) if system == "name" else {}

    def label(self, cell):
        return f"colour {cell!r}" if self.system == "name" else f"{dict(bricklink='BrickLink', rebrickable='Rebrickable', ldraw='LDraw')[self.system]} colour {cell}"

    def resolve(self, cell):
        cell = str(cell).strip()
        number = int(cell) if re.fullmatch(r"-?\d+", cell) else None
        if self.system == "ldraw":
            return (number, True, 0) if number is not None and number >= 0 else None
        if self.system == "rebrickable":
            entry = self.maps["colours"].get(number) if number is not None else None
            if entry is None or number in self.maps["unmapped_colours"]:
                return None
            return int(entry["ldraw"]), bool(entry.get("exact", True)), number
        # BrickLink ids; "name" accepts BrickLink names, then Rebrickable names
        if number is not None and self.system == "bricklink":
            entry = self.bl["colours"].get(number)
        else:
            bl_id = self.bl["by_name"].get(norm_colour_name(cell)) if cell else None
            entry = self.bl["colours"].get(bl_id) if bl_id is not None else None
        if entry is not None:
            return int(entry["ldraw"]), bool(entry.get("exact", True)), 0
        if self.system == "name" and cell:
            hit = self.rb_names.get(norm_colour_name(cell))
            if hit:
                return int(hit[1]["ldraw"]), bool(hit[1].get("exact", True)), hit[0]
        return None


# --------------------------------------------------------------------- the shared mapper

def _to_qty(cell, line, what="quantity"):
    cell = str(cell).strip()
    if re.fullmatch(r"\d+(\.0+)?", cell):
        return int(float(cell))
    raise ParseError(f"Line {line}: {what} must be a whole number, got {cell!r}")


def _build(items, *, system, library, maps, dialect, fmt, spare_note=""):
    """items: iterable of (line, part, colour cell, qty, reason or ''); ``reason`` marks lines to leave out before mapping."""
    maps = maps or load_maps()
    colours = _Colours(system, maps)
    result = ParseResult(dialect=dialect, warnings=list(maps["warnings"]) + (list(colours.bl["warnings"]) if colours.bl else []))
    owned, unmapped_p, unmapped_c, approximated, zero = {}, Counter(), Counter(), Counter(), []
    reasons_p, reasons_c = {}, {}
    for line, part, cell, qty, skip in items:
        result.total_rows += 1
        if result.total_rows > MAX_ROWS:
            raise ParseError(f"File has more than {MAX_ROWS} rows")
        if qty == 0:
            zero.append(line)
            continue
        result.total_pieces += qty
        if skip:
            unmapped_p[(part, cell)] += qty
            reasons_p[(part, cell)] = skip
            continue
        ldraw = _lookup_part(part, maps, library)
        if ldraw is None:
            unmapped_p[(part, cell)] += qty
            reasons_p[(part, cell)] = "no LDraw part with this number"
            continue
        found = colours.resolve(cell)
        if found is None:
            unmapped_c[(cell, part)] += qty
            reasons_c[(cell, part)] = f"{colours.label(cell)} has no LDraw colour"
            continue
        code, exact, rb_colour = found
        if not exact:
            approximated[cell] += qty
        key = (ldraw, code)
        if key in owned:
            owned[key][0] += qty
        else:
            owned[key] = [qty, part if system == "rebrickable" else "", rb_colour]
    if result.total_rows == 0:
        raise ParseError("The file has no usable lines")
    result.rows = [Row(p, c, v[0], v[1], v[2]) for (p, c), v in sorted(owned.items())]
    result.unmapped_parts = [dict(rb_part=p, colour=c, qty=q, reason=reasons_p[(p, c)])
                             for (p, c), q in sorted(unmapped_p.items(), key=lambda kv: (-kv[1], kv[0]))]
    result.unmapped_colours = [dict(rb_colour=c, part=p, qty=q, reason=reasons_c[(c, p)])
                               for (c, p), q in sorted(unmapped_c.items(), key=lambda kv: (-kv[1], str(kv[0])))]
    if zero:
        result.warnings.append(f"Skipped {len(zero)} line(s) with quantity 0 (line {', '.join(map(str, zero[:5]))}{'...' if len(zero) > 5 else ''})")
    if spare_note:
        result.warnings.append(spare_note)
    if approximated:
        result.warnings.append(f"{sum(approximated.values())} piece(s) in {len(approximated)} colour(s) use an approximate LDraw colour ({', '.join(map(str, sorted(approximated, key=str)))})")
    result.origin = {"format": fmt}
    return result


# --------------------------------------------------------------------- XML (BrickLink, BrickStore)

def _xml_root(text):
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig")
    text = text.lstrip("﻿")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ParseError(f"File exceeds the {MAX_BYTES // (1024 * 1024)} MB limit")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.IGNORECASE):
        raise ParseError("XML with a DOCTYPE or entity declarations is not accepted")
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise ParseError(f"Malformed XML: {exc}") from exc


def xml_format(text):
    """"bricklink-xml", "brickstore-bsx", or None for text that is not one of them."""
    head = (text.decode("utf-8-sig", errors="replace") if isinstance(text, bytes) else text).lstrip("﻿").lstrip()
    if not head.startswith("<"):
        return None
    tag = re.search(r"<([A-Za-z_][\w.-]*)", re.sub(r"<\?.*?\?>|<!--.*?-->", "", head[:4000], flags=re.DOTALL))
    name = tag.group(1).upper() if tag else ""
    return {"INVENTORY": "bricklink-xml", "BRICKSTOREXML": "brickstore-bsx"}.get(name)


def _fields(item):
    return {child.tag.upper(): (child.text or "").strip() for child in item}


def _qty_columns(present, kind):
    """Which quantity field says what is owned. Wanted-list quantities alone are not ownership."""
    for names, note in ((_QTY_OWNED, ""), (_QTY_FILLED, "Counted the filled quantity (QTYFILLED) as owned; the wanted quantity is ignored")):
        for name in names:
            if name in present:
                return name, note
    raise ParseError(f"This {kind} lists wanted quantities (MINQTY) only, not what you own. Export an inventory, "
                     "or a wanted list that has filled quantities, or use a CSV with your own counts")


def parse_bricklink_xml(text, *, library=None, maps=None):
    root = _xml_root(text)
    if root.tag.upper() != "INVENTORY":
        raise ParseError(f"Not a BrickLink XML file: expected <INVENTORY>, found <{root.tag}>")
    items = [_fields(i) for i in root if i.tag.upper() == "ITEM"]
    if not items:
        raise ParseError("The BrickLink XML has no <ITEM> entries")
    present = {k.lower() for i in items for k in i}
    qty_key, note = _qty_columns(present, "BrickLink wanted list")

    def lines():
        for n, item in enumerate(items, 1):
            part, kind = item.get("ITEMID", ""), (item.get("ITEMTYPE") or "P").upper()
            if not part:
                raise ParseError(f"Item {n}: <ITEMID> is missing")
            qty = _to_qty(item.get(qty_key.upper()) or "0", f"item {n}")
            skip = "" if kind == "P" else f"BrickLink item type {kind} ({ITEM_TYPES.get(kind, 'other')}) is not a loose part"
            yield n, part, item.get("COLOR", ""), qty, skip
    return _build(lines(), system="bricklink", library=library, maps=maps, dialect="bricklink-xml", fmt="bricklink-xml", spare_note=note)


def parse_brickstore_bsx(text, *, library=None, maps=None):
    root = _xml_root(text)
    if root.tag.upper() != "BRICKSTOREXML":
        raise ParseError(f"Not a BrickStore file: expected <BrickStoreXML>, found <{root.tag}>")
    inventory = next((c for c in root if c.tag.upper() == "INVENTORY"), None)
    if inventory is None:
        raise ParseError("The BrickStore file has no <Inventory>")
    items = [_fields(i) for i in inventory if i.tag.upper() == "ITEM"]
    if not items:
        raise ParseError("The BrickStore inventory has no <Item> entries")
    excluded = 0

    def lines():
        nonlocal excluded
        for n, item in enumerate(items, 1):
            part, kind = item.get("ITEMID", ""), (item.get("ITEMTYPEID") or "P").upper()
            if not part:
                raise ParseError(f"Item {n}: <ItemID> is missing")
            if item.get("STATUS", "").upper() == "X":      # BrickStore's "exclude" flag: the owner took it out of the list
                excluded += 1
                continue
            qty = _to_qty(item.get("QTY") or "0", f"item {n}")
            skip = "" if kind == "P" else f"BrickStore item type {kind} ({ITEM_TYPES.get(kind, 'other')}) is not a loose part"
            cell = item.get("COLORID") or item.get("COLORNAME", "")
            yield n, part, cell, qty, skip
    result = _build(lines(), system="bricklink", library=library, maps=maps, dialect="brickstore-bsx", fmt="brickstore-bsx")
    if excluded:
        result.warnings.append(f"Left out {excluded} item(s) BrickStore marks as excluded")
    return result


# --------------------------------------------------------------------- CSV (BrickLink, generic)

def _read_csv(text):
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig")
    text = text.lstrip("﻿")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ParseError(f"File exceeds the {MAX_BYTES // (1024 * 1024)} MB limit")
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip()), None)
    if start is None:
        raise ParseError("File is empty")
    first = lines[start]
    counts = {d: first.count(d) for d in (",", ";", "\t")}
    delimiter = max(counts, key=lambda d: counts[d]) if max(counts.values()) else ","
    reader = csv.reader(io.StringIO("\n".join(lines[start:])), delimiter=delimiter)
    try:
        header = next(reader)
    except (StopIteration, csv.Error) as exc:
        raise ParseError(f"Cannot read header: {exc}") from exc
    return header, reader, start


def header_signature(headers):
    """A short stable id of a header row, so a column choice can be remembered for the next file with the same layout."""
    return hashlib.sha1("|".join(_norm(h) for h in headers).encode("utf-8")).hexdigest()[:16]


def csv_columns(headers):
    """Normalised header names, the way every CSV importer sees them."""
    return [_norm(h) for h in headers]


def is_bricklink_csv(headers):
    h = set(csv_columns(headers))
    return bool(h & _BL_CSV_ITEM) and bool(h & _COLOUR) and bool(h & (set(_QTY_OWNED) | set(_QTY_FILLED) | set(_QTY_WANTED)))


def _csv_lines(reader, start, ip, ic, iq, qty_what="quantity"):
    need = max(ip, ic, iq)
    try:
        for row in reader:
            line = reader.line_num + start
            if not row or not any(c.strip() for c in row):
                continue
            if len(row) <= need:
                raise ParseError(f"Line {line}: expected at least {need + 1} columns, got {len(row)}")
            part = row[ip].strip()
            if not part:
                raise ParseError(f"Line {line}: empty part number")
            yield line, part, row[ic].strip(), _to_qty(row[iq] or "0", line, qty_what), ""
    except csv.Error as exc:
        raise ParseError(f"Malformed CSV: {exc}") from exc


def parse_bricklink_csv(text, *, library=None, maps=None):
    header, reader, start = _read_csv(text)
    cols = csv_columns(header)
    if not is_bricklink_csv(header):
        raise ParseError("Unrecognised header %r: expected BrickLink columns Item No, Color, Qty" % ",".join(header)[:120])
    ip = _find(cols, _BL_CSV_ITEM)
    ic = _find(cols, _COLOUR)
    qty_key, note = _qty_columns(set(cols), "BrickLink CSV")
    iq = cols.index(qty_key)
    system = "bricklink" if cols[ic] in {"colorid", "colourid"} else "name"
    return _build(_csv_lines(reader, start, ip, ic, iq), system=system, library=library, maps=maps, dialect="bricklink-csv",
                  fmt="bricklink-csv", spare_note=note)


def guess_mapping(headers, rows=()):
    """The columns that look like the part, colour and quantity, and the colour system the colour cells look like."""
    cols = csv_columns(headers)
    guess = {}
    for role, names in _SPLIT_SYNONYMS.items():
        i = _find(cols, names)
        if i is not None and headers[i] not in guess.values():
            guess[role] = headers[i]
    colour = headers.index(guess["colour"]) if "colour" in guess else None
    cells = [r[colour].strip() for r in rows if colour is not None and len(r) > colour and r[colour].strip()]
    if cells:
        guess["system"] = "name" if not all(re.fullmatch(r"-?\d+", c) for c in cells) else ("bricklink" if cols[colour] in {"colorid", "colourid"} else "")
    return guess


def parse_generic_csv(text, mapping=None, *, library=None, maps=None):
    """Any CSV, with the owner's choice of columns: ``mapping = {"part": header, "colour": header, "qty": header, "system": ...}``."""
    header, reader, start = _read_csv(text)
    if not mapping:
        rows = []
        for row in reader:
            if any(c.strip() for c in row):
                rows.append(row)
            if len(rows) >= 5:
                break
        raise NeedsMapping("Unrecognised header %r: choose which columns hold the part, the colour and the quantity" % ",".join(header)[:120],
                           header, rows, guess_mapping(header, rows))
    index = {}
    for role in ("part", "colour", "qty"):
        name = mapping.get(role)
        if name not in header:
            raise ParseError(f"Column mapping: choose the {role} column (one of {', '.join(repr(h) for h in header[:12])})")
        index[role] = header.index(name)
    if len(set(index.values())) < 3:
        raise ParseError("Column mapping: the part, colour and quantity columns must be three different columns")
    system = mapping.get("system") or "name"
    if system not in COLOUR_SYSTEMS:
        raise ParseError(f"Column mapping: colour system must be one of {', '.join(COLOUR_SYSTEMS)}")
    result = _build(_csv_lines(reader, start, index["part"], index["colour"], index["qty"]), system=system, library=library, maps=maps,
                    dialect="generic-csv", fmt="generic-csv")
    result.origin["mapping"] = dict(part=mapping["part"], colour=mapping["colour"], qty=mapping["qty"], system=system)
    result.origin["header_signature"] = header_signature(header)
    return result


__all__ = ["COLOUR_SYSTEMS", "NeedsMapping", "csv_columns", "guess_mapping", "is_bricklink_csv", "load_bricklink_colours",
           "parse_bricklink_csv", "parse_bricklink_xml", "parse_brickstore_bsx", "parse_generic_csv", "xml_format"]
