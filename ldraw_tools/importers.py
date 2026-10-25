"""Importer registry: one place that knows which file formats the parts collection can read.

Every importer is deterministic Python: ``detect(text)`` names the format of an upload, ``parse(text, ...)`` returns a
``ParseResult`` (exact rows, ``review`` items for colour families, unmapped lines) tagged with ``origin.format``.
Nothing is stored here: callers show a preview first and write only when the person confirms (the web backend does this
in ``collection.py``). A new format plugs in by adding an entry to ``FORMATS`` and a branch to ``parse``. ``generic-csv`` is never
detected: the owner names the part, colour and quantity columns (``mapping``) and the colour system.
"""
from __future__ import annotations

import csv
import io

from . import bricklink
from .bricklink import NeedsMapping
from .inventory import ParseError, ParseResult, _norm, parse_rebrickable_csv

FORMATS = {
    "rebrickable-csv": "Rebrickable parts list or database CSV",
    "brickit-pileometer": "Brickit / Pileometer CSV (colour families)",
    "bricklink-xml": "BrickLink XML (inventory or wanted list)",
    "brickstore-bsx": "BrickStore BSX",
    "bricklink-csv": "BrickLink CSV (Item No, Color, Qty)",
    "generic-csv": "Any CSV (you choose the columns)",
}


def detect(text: str | bytes) -> str | None:
    """The format of an upload by its header row, or None when no importer understands it."""
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig", errors="replace")
    first = next((line for line in text.lstrip("﻿").splitlines() if line.strip()), "")
    if not first:
        return None
    xml = bricklink.xml_format(text)
    if xml:
        return xml
    delimiter = ";" if first.count(";") > first.count(",") else ","
    try:
        raw_headers = next(csv.reader(io.StringIO(first), delimiter=delimiter))
        headers = [_norm(h) for h in raw_headers]
    except (StopIteration, csv.Error):
        return None
    has = lambda names: any(h in names for h in headers)  # noqa: E731
    if "scan" in headers and "partname" in headers and has({"part", "partnum", "partnumber", "partid", "partno"}):
        return "brickit-pileometer"
    if has({"part", "partnum", "partnumber", "partid", "partno"}) and has({"color", "colour", "colorid", "colourid"}) \
            and has({"quantity", "qty"}):
        return "rebrickable-csv"
    if bricklink.is_bricklink_csv(raw_headers):
        return "bricklink-csv"
    return None


def parse(text: str | bytes, *, include_spares: bool = False, library=None, family_colours: dict | None = None,
          filename: str = "", format: str | None = None, mapping: dict | None = None) -> ParseResult:
    """Parse an upload with the importer that detects it (or the one named by ``format``); ParseError when none does.

    A CSV no importer recognises raises ``NeedsMapping`` (a ParseError) carrying its headers, a few rows and a guess, so the
    caller can ask which columns to use and call again with ``mapping`` (``format="generic-csv"`` is implied by a mapping)."""
    if format is not None and format not in FORMATS:
        raise ParseError(f"Unknown import format {format!r}; choose one of {', '.join(FORMATS)}")
    fmt = format or ("generic-csv" if mapping else detect(text))
    if fmt == "bricklink-xml":
        result = bricklink.parse_bricklink_xml(text, library=library)
    elif fmt == "brickstore-bsx":
        result = bricklink.parse_brickstore_bsx(text, library=library)
    elif fmt == "bricklink-csv":
        result = bricklink.parse_bricklink_csv(text, library=library)
    elif fmt == "generic-csv":
        result = bricklink.parse_generic_csv(text, mapping, library=library)
    elif fmt is None:
        try:
            # parse_rebrickable_csv words the header problem precisely; keep its message
            return parse_rebrickable_csv(text, include_spares=include_spares, library=library, family_colours=family_colours)
        except ParseError as exc:
            if "Unrecognised header" not in str(exc):
                raise
            bricklink.parse_generic_csv(text, None, library=library)     # raises NeedsMapping with the headers and a guess
            raise
    else:
        result = parse_rebrickable_csv(text, include_spares=include_spares, library=library, family_colours=family_colours)
    if result.origin is None or not result.origin.get("format"):
        result.origin = {**(result.origin or {}), "format": fmt}
    if filename:
        result.origin["file"] = filename
    if result.pileometer:
        result.origin["scans"] = result.pileometer.get("scans")
    return result


__all__ = ["FORMATS", "NeedsMapping", "ParseError", "detect", "parse"]
