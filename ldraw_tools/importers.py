"""Importer registry: one place that knows which file formats the parts collection can read.

Every importer is deterministic Python: ``detect(text)`` names the format of an upload, ``parse(text, ...)`` returns a
``ParseResult`` (exact rows, ``review`` items for colour families, unmapped lines) tagged with ``origin.format``.
Nothing is stored here: callers show a preview first and write only when the person confirms (the web backend does this
in ``collection.py``). New formats (BrickLink XML, BrickStore, generic CSV) plug in by adding an entry to ``FORMATS``.
"""
from __future__ import annotations

import csv
import io

from .inventory import ParseError, ParseResult, _norm, parse_rebrickable_csv

FORMATS = {
    "rebrickable-csv": "Rebrickable parts list or database CSV",
    "brickit-pileometer": "Brickit / Pileometer CSV (colour families)",
}


def detect(text: str | bytes) -> str | None:
    """The format of an upload by its header row, or None when no importer understands it."""
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig", errors="replace")
    first = next((line for line in text.lstrip("﻿").splitlines() if line.strip()), "")
    if not first:
        return None
    delimiter = ";" if first.count(";") > first.count(",") else ","
    try:
        headers = [_norm(h) for h in next(csv.reader(io.StringIO(first), delimiter=delimiter))]
    except (StopIteration, csv.Error):
        return None
    has = lambda names: any(h in names for h in headers)  # noqa: E731
    if "scan" in headers and "partname" in headers and has({"part", "partnum", "partnumber", "partid", "partno"}):
        return "brickit-pileometer"
    if has({"part", "partnum", "partnumber", "partid", "partno"}) and has({"color", "colour", "colorid", "colourid"}) \
            and has({"quantity", "qty"}):
        return "rebrickable-csv"
    return None


def parse(text: str | bytes, *, include_spares: bool = False, library=None, family_colours: dict | None = None,
          filename: str = "") -> ParseResult:
    """Parse an upload with the importer that detects it; ParseError when none does."""
    fmt = detect(text)
    if fmt is None:
        # parse_rebrickable_csv words the header problem precisely; keep its message
        return parse_rebrickable_csv(text, include_spares=include_spares, library=library, family_colours=family_colours)
    result = parse_rebrickable_csv(text, include_spares=include_spares, library=library, family_colours=family_colours)
    result.origin = {"format": fmt, **({"file": filename} if filename else {})}
    if result.pileometer:
        result.origin["scans"] = result.pileometer.get("scans")
    return result


__all__ = ["FORMATS", "ParseError", "detect", "parse"]
