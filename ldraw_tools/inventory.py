"""User parts inventories: Rebrickable CSV parsing, queries and model checks.

Quantity semantics are owned counts.  Rows are expressed in LDraw terms (part
number without ``.dat`` plus LDraw colour code) so they compare directly with
the physical BOM of a model.  Rebrickable -> LDraw translation uses the data
files ``data/rebrickable-colours.json`` and ``data/rebrickable-parts.json``.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, fields
from functools import lru_cache
from pathlib import Path

from .common import DATA

COLOURS_FILE = DATA / "rebrickable-colours.json"
PARTS_FILE = DATA / "rebrickable-parts.json"
MAX_ROWS = 500_000
MAX_BYTES = 25 * 1024 * 1024
MODES = ("prefer", "only")


class ParseError(ValueError):
    """The uploaded file is not a usable Rebrickable inventory CSV."""


@dataclass
class Row:
    part: str
    colour: int
    qty: int
    rb_part: str = ""
    rb_colour: int = 0
    family: str = ""    # source colour family when the colour is only a default for it (Pileometer): colour approximate
    note: str = ""      # free text about this row ("blue tub"); optional, never read by counting code

    def to_json(self):
        out = dict(part=self.part, colour=self.colour, qty=self.qty, rb_part=self.rb_part, rb_colour=self.rb_colour)
        if self.family:
            out.update(family=self.family, colour_approximate=True)
        if self.note:
            out["note"] = self.note
        return out

    @classmethod
    def from_json(cls, data):
        return cls(str(data["part"]), int(data["colour"]), int(data["qty"]), str(data.get("rb_part", "")), int(data.get("rb_colour", 0)),
                   str(data.get("family", "")), str(data.get("note", "")))


@dataclass
class ParseResult:
    rows: list[Row] = field(default_factory=list)
    unmapped_parts: list[dict] = field(default_factory=list)    # {rb_part, colour, qty}
    unmapped_colours: list[dict] = field(default_factory=list)  # {rb_colour, part, qty}
    warnings: list[str] = field(default_factory=list)
    dialect: str = ""
    total_rows: int = 0      # data rows read (excluding blank lines)
    total_pieces: int = 0    # pieces counted as owned in the file, mapped or not
    spare_pieces: int = 0    # pieces on spare rows (inside total_pieces only with include_spares)

    pileometer: dict | None = None    # Pileometer imports: scans, rows and the colour family mapping used
    review: list[dict] = field(default_factory=list)    # approximate items (colour family only): kept, never counted as owned
    origin: dict | None = None        # where the data came from: {"format": "rebrickable-csv" | "brickit-pileometer", ...}

    def to_json(self):
        out = dict(rows=[r.to_json() for r in self.rows], unmapped_parts=self.unmapped_parts, unmapped_colours=self.unmapped_colours,
                   warnings=self.warnings, dialect=self.dialect, total_rows=self.total_rows,
                   total_pieces=self.total_pieces, spare_pieces=self.spare_pieces)
        if self.origin:
            out["origin"] = self.origin
        if self.pileometer:
            out["pileometer"] = self.pileometer
        if self.review:
            out["review"] = self.review
        return out

    @classmethod
    def from_json(cls, data):
        return cls(rows=[Row.from_json(r) for r in data.get("rows", [])], unmapped_parts=list(data.get("unmapped_parts", [])),
                   unmapped_colours=list(data.get("unmapped_colours", [])), warnings=list(data.get("warnings", [])),
                   dialect=data.get("dialect", ""), total_rows=int(data.get("total_rows", 0)),
                   total_pieces=int(data.get("total_pieces", 0)), spare_pieces=int(data.get("spare_pieces", 0)),
                   pileometer=data.get("pileometer"), review=list(data.get("review", [])), origin=data.get("origin"))


@dataclass
class Inventory:
    id: str
    name: str
    created_at: float = 0.0
    source_filename: str = ""
    rows: list[Row] = field(default_factory=list)
    unmapped_parts: list[dict] = field(default_factory=list)
    unmapped_colours: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    review: list[dict] = field(default_factory=list)    # colour-family items waiting for an exact colour; not in rows, not counted
    kind: str = ""              # "import" (absent), "set" (parts of a catalogue set) or "loose" (manual and quick adds)
    set: dict | None = None     # kind "set": {set_num, name, year, theme, copies, state: "parts" | "built", ...}
    origin: dict | None = None  # {"format": ..., "scan": ..., "file": ...}
    schema: int = 0             # 2 once the backend has edited the file; absent is 1

    @property
    def total_pieces(self):
        return sum(r.qty for r in self.rows)

    @property
    def distinct_parts(self):
        return len({r.part for r in self.rows})

    @property
    def distinct_colours(self):
        return len({r.colour for r in self.rows})

    @property
    def unmapped_pieces(self):
        return sum(u["qty"] for u in self.unmapped_parts) + sum(u["qty"] for u in self.unmapped_colours)

    def to_json(self):
        return dict(id=self.id, name=self.name, created_at=self.created_at, source_filename=self.source_filename,
                    rows=[r.to_json() for r in self.rows], unmapped_parts=self.unmapped_parts,
                    unmapped_colours=self.unmapped_colours, warnings=self.warnings,
                    **({"review": self.review} if self.review else {}),
                    **{k: v for k, v in (("kind", self.kind), ("set", self.set), ("origin", self.origin), ("schema", self.schema)) if v},
                    totals=dict(pieces=self.total_pieces, distinct_parts=self.distinct_parts, distinct_colours=self.distinct_colours,
                                unmapped_pieces=self.unmapped_pieces))

    @classmethod
    def from_json(cls, data):
        known = {f.name for f in fields(cls)}
        extra = {k: v for k, v in data.items() if k in known and k not in {"rows", "created_at"}}
        extra.update(rows=[Row.from_json(r) for r in data.get("rows", [])], created_at=float(data.get("created_at", 0.0)))
        return cls(**extra)


# ---------------------------------------------------------------- mapping data

@lru_cache(maxsize=4)
def _load_maps(colours_file, parts_file):
    maps = dict(colours={}, unmapped_colours=set(), aliases={}, no_ldraw=set(), warnings=[])
    for label, path in (("colour", colours_file), ("part", parts_file)):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            maps["warnings"].append(f"Rebrickable {label} mapping file {Path(path).name} is missing; no {label} translation applied")
            continue
        except (OSError, ValueError) as exc:
            maps["warnings"].append(f"Rebrickable {label} mapping file {Path(path).name} is unreadable ({exc})")
            continue
        if label == "colour":
            maps["colours"] = {int(k): v for k, v in (data.get("colours") or {}).items()}
            maps["unmapped_colours"] = {int(x) for x in data.get("unmapped") or []}
        else:
            maps["aliases"] = {str(k).casefold(): str(v).casefold().removesuffix(".dat") for k, v in (data.get("aliases") or {}).items()}
            maps["no_ldraw"] = {str(x).casefold() for x in data.get("no_ldraw") or []}
    return maps


def load_maps():
    """Cached mapping tables; see clear_map_cache() after changing COLOURS_FILE/PARTS_FILE."""
    override = os.environ.get("LDRAW_NOVA_RB_MAP_DIR")  # alternate directory holding both mapping files (tests, custom maps)
    if override:
        return _load_maps(str(Path(override) / COLOURS_FILE.name), str(Path(override) / PARTS_FILE.name))
    return _load_maps(str(COLOURS_FILE), str(PARTS_FILE))


def clear_map_cache():
    _load_maps.cache_clear()


# --------------------------------------------------------------------- parsing

def _norm(header):
    return re.sub(r"[\s_?\-]+", "", header.strip().lower())


_PART = {"part", "partnum", "partnumber", "partid", "partno"}
_COLOUR = {"color", "colour", "colorid", "colourid"}
_QTY = {"quantity", "qty"}
_SPARE = {"isspare", "spare"}
_TRUE = {"true", "t", "1", "yes", "y"}
_FALSE = {"false", "f", "0", "no", "n", ""}


def _find(headers, names):
    for i, h in enumerate(headers):
        if h in names:
            return i
    return None


def _int(cell, what, line, *, negative_ok=False):
    cell = cell.strip()
    if not re.fullmatch(r"[+-]?\d+", cell):
        raise ParseError(f"Line {line}: {what} must be an integer, got {cell!r}")
    value = int(cell)
    if value < 0 and not negative_ok:
        raise ParseError(f"Line {line}: {what} must not be negative, got {value}")
    return value


def _ldraw_part(rb_part, maps, library):
    key = rb_part.casefold()
    if key in maps["no_ldraw"]:
        return None
    target = maps["aliases"].get(key, key)
    if library is not None and target not in library.by_code:
        return None
    return target


_PRINT_SUFFIX = re.compile(r"(?:pr|pat|pb|c)\d+.*$")
_MOVED = re.compile(r"~?moved to\s+([0-9a-z]+)", re.IGNORECASE)


def _lookup_part(number, maps, library):
    """LDraw part for a source part number: alias data first, then print/pattern suffix, trailing letter and '~Moved to' redirects."""
    key = number.strip().casefold().removesuffix(".dat")
    tried = []
    for candidate in (key, _PRINT_SUFFIX.sub("", key), key.rstrip("abcdefghijklmnopqrstuvwxyz") if key[:1].isdigit() else key):
        if candidate and candidate not in tried:
            tried.append(candidate)
    for candidate in tried:
        part = _ldraw_part(candidate, maps, library)
        if part is None:
            continue
        for _ in range(3):      # follow "~Moved to" stubs
            moved = _MOVED.match(library.by_code.get(part, "")) if library is not None else None
            if not moved:
                break
            part = moved.group(1).casefold()
        if library is None or part in library.by_code:
            return part
    return None


def parse_rebrickable_csv(text, *, include_spares=False, library=None, maps=None, family_colours=None):
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig")
    if len(text) > MAX_BYTES or len(text.encode("utf-8")) > MAX_BYTES:
        raise ParseError(f"File exceeds the {MAX_BYTES // (1024 * 1024)} MB limit")
    text = text.lstrip("﻿")
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip()), None)
    if start is None:
        raise ParseError("File is empty")
    first = lines[start]
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.reader(io.StringIO("\n".join(lines[start:])), delimiter=delimiter)
    try:
        header = next(reader)
    except (StopIteration, csv.Error) as exc:
        raise ParseError(f"Cannot read header: {exc}") from exc
    headers = [_norm(h) for h in header]
    if "scan" in headers and "partname" in headers and _find(headers, _PART) is not None:
        return _parse_pileometer(reader, headers, start, library, maps or load_maps(), family_colours)
    ip, ic, iq, isp = _find(headers, _PART), _find(headers, _COLOUR), _find(headers, _QTY), _find(headers, _SPARE)
    if ip is None or ic is None or iq is None:
        raise ParseError("Unrecognised header %r: expected Rebrickable columns Part, Color, Quantity "
                         "(or part_num, color_id, quantity)" % ",".join(header)[:120])
    database = "partnum" in headers or "inventoryid" in headers or "colorid" in headers
    maps = maps or load_maps()
    result = ParseResult(dialect="rebrickable-database" if database else "rebrickable-parts-list", warnings=list(maps["warnings"]))
    owned = {}            # (ldraw part, ldraw colour) -> [qty, rb_part, rb_colour]
    unmapped_p, unmapped_c = Counter(), Counter()
    zero_rows, approximated, spare_rows = [], Counter(), 0
    need = max(ip, ic, iq)
    try:
        for row in reader:
            line = reader.line_num + start
            if not row or not any(c.strip() for c in row):
                continue
            if len(row) <= need:
                raise ParseError(f"Line {line}: expected at least {need + 1} columns, got {len(row)}")
            result.total_rows += 1
            if result.total_rows > MAX_ROWS:
                raise ParseError(f"File has more than {MAX_ROWS} rows")
            rb_part = row[ip].strip()
            if not rb_part:
                raise ParseError(f"Line {line}: empty part number")
            rb_colour = _int(row[ic], "colour", line, negative_ok=True)
            qty = _int(row[iq], "quantity", line)
            spare = False
            if isp is not None and isp < len(row):
                flag = row[isp].strip().lower()
                if flag not in _TRUE | _FALSE:
                    raise ParseError(f"Line {line}: is_spare must be true/false, got {row[isp]!r}")
                spare = flag in _TRUE
            if qty == 0:
                zero_rows.append(line)
                continue
            if spare:
                result.spare_pieces += qty
                spare_rows += 1
                if not include_spares:
                    continue
            result.total_pieces += qty
            part = _ldraw_part(rb_part, maps, library)
            if part is None:
                unmapped_p[(rb_part, rb_colour)] += qty
                continue
            entry = maps["colours"].get(rb_colour)
            if entry is None or rb_colour in maps["unmapped_colours"]:
                unmapped_c[(rb_colour, rb_part)] += qty
                continue
            if not entry.get("exact", True):
                approximated[rb_colour] += qty
            key = (part, int(entry["ldraw"]))
            if key in owned:
                owned[key][0] += qty
            else:
                owned[key] = [qty, rb_part, rb_colour]
    except csv.Error as exc:
        raise ParseError(f"Malformed CSV: {exc}") from exc
    if result.total_rows == 0:
        raise ParseError("File has a header but no data rows")
    result.rows = [Row(p, c, v[0], v[1], v[2]) for (p, c), v in sorted(owned.items())]
    result.unmapped_parts = [dict(rb_part=p, colour=c, qty=q) for (p, c), q in sorted(unmapped_p.items(), key=lambda kv: (-kv[1], kv[0]))]
    result.unmapped_colours = [dict(rb_colour=c, part=p, qty=q) for (c, p), q in sorted(unmapped_c.items(), key=lambda kv: (-kv[1], kv[0]))]
    if zero_rows:
        result.warnings.append(f"Skipped {len(zero_rows)} row(s) with quantity 0 (line {', '.join(map(str, zero_rows[:5]))}{'...' if len(zero_rows) > 5 else ''})")
    if spare_rows and not include_spares:
        result.warnings.append(f"{result.spare_pieces} spare piece(s) on {spare_rows} row(s) not counted as owned (use include_spares to count them)")
    if approximated:
        result.warnings.append(f"{sum(approximated.values())} piece(s) in {len(approximated)} colour(s) use an approximate LDraw colour (rb ids {', '.join(map(str, sorted(approximated)))})")
    return result

# Brickit's Pileometer exports a colour family, not an exact colour. Pieces stay in `review` (maybe pieces,
# never counted as owned) until the family is assigned an LDraw colour; then they move into `rows`.
# Candidates: LDraw codes a family may stand for, most likely first.
FAMILY_CANDIDATES = {"Whites": [15], "Yellows": [14, 226], "Reds": [4, 320], "Blues": [1, 272, 73], "Grays": [71, 72], "Blacks": [0],
                     "Greens": [2, 10, 288], "Browns": [70, 6, 19], "Pinks": [29, 13]}


def _parse_pileometer(reader, headers, start, library, maps, family_colours):
    """Brickit Pileometer CSV: Scan, Part, PartName, Color (family), Quantity. Quantities add up across scans."""
    ip, ic, iq = _find(headers, _PART), _find(headers, _COLOUR), _find(headers, _QTY)
    iscan, iname = headers.index("scan"), headers.index("partname")
    if ic is None or iq is None:
        raise ParseError("Unrecognised Pileometer header: expected Scan, Part, PartName, Color, Quantity")
    assigned = {}
    for family, code in (family_colours or {}).items():
        if family not in FAMILY_CANDIDATES:
            raise ParseError(f"Unknown colour family {family!r}; expected one of {', '.join(FAMILY_CANDIDATES)}")
        code = int(code)
        if library is not None and (code not in library.colours_by_code or code in (16, 24)):
            raise ParseError(f"Colour {code} is not an installed LDraw colour")
        assigned[family] = code
    result = ParseResult(dialect="pileometer", warnings=list(maps["warnings"]))
    owned, maybe, unmatched, families, scans, unmatched_rows = {}, {}, {}, {}, set(), 0
    need = max(ip, ic, iq, iscan, iname)
    try:
        for row in reader:
            line = reader.line_num + start
            if not row or not any(c.strip() for c in row):
                continue
            if len(row) <= need:
                raise ParseError(f"Line {line}: expected at least {need + 1} columns, got {len(row)}")
            result.total_rows += 1
            if result.total_rows > MAX_ROWS:
                raise ParseError(f"File has more than {MAX_ROWS} rows")
            number, name, family = row[ip].strip(), row[iname].strip(), row[ic].strip().title()
            if not number:
                raise ParseError(f"Line {line}: empty part number")
            qty = _int(row[iq], "quantity", line)
            scan = row[iscan].strip()
            scans.add(scan)
            if qty == 0:
                continue
            result.total_pieces += qty
            info = families.setdefault(family, dict(rows=0, pieces=0))
            info["rows"] += 1
            info["pieces"] += qty
            if family not in FAMILY_CANDIDATES:
                result.unmapped_colours.append(dict(rb_colour=0, part=number, qty=qty, family=family))
                continue
            part = _lookup_part(number, maps, library)
            if part is None:
                unmatched_rows += 1
                entry = unmatched.setdefault((number, family), dict(rb_part=number, colour=assigned.get(family, 0), qty=0, name=name, family=family))
                entry["qty"] += qty
            elif family in assigned:
                key = (part, assigned[family])
                if key in owned:
                    owned[key].qty += qty
                else:
                    owned[key] = Row(part, assigned[family], qty, number, 0, family)
            else:
                item = maybe.setdefault((part, family), dict(part=part, family=family, candidates=FAMILY_CANDIDATES[family], qty=0,
                                                              brickit_part=number, scans=[]))
                item["qty"] += qty
                if scan not in item["scans"]:
                    item["scans"].append(scan)
    except csv.Error as exc:
        raise ParseError(f"Malformed CSV: {exc}") from exc
    if result.total_rows == 0:
        raise ParseError("File has a header but no data rows")
    result.rows = sorted(owned.values(), key=lambda r: (r.part, r.colour))
    result.review = [dict(id=f"r{i}", note="scan " + ", ".join(m.pop("scans")), **m)
                     for i, m in enumerate(sorted(maybe.values(), key=lambda m: (m["family"], m["part"])), 1)]
    result.unmapped_parts = sorted(unmatched.values(), key=lambda u: (-u["qty"], u["rb_part"]))

    def colour_label(code):
        known = library.colours_by_code.get(code) if library is not None else None
        return (known.name or "").replace("_", " ") if known else colour_name(code)

    result.pileometer = dict(
        scans=len(scans), rows=result.total_rows, pieces=result.total_pieces, matched_rows=result.total_rows - unmatched_rows,
        assigned_pieces=sum(r.qty for r in result.rows), review_pieces=sum(r["qty"] for r in result.review),
        families=[dict(family=f, colour=assigned.get(f), colour_name=colour_label(assigned[f]) if f in assigned else "",
                       candidates=[dict(colour=c, name=colour_label(c)) for c in FAMILY_CANDIDATES.get(f, [])], **info)
                  for f, info in sorted(families.items())])
    if result.review:
        result.warnings.append("Pileometer gives colour families, not exact colours: those pieces wait in the review list "
                               "and are not counted as owned until a colour is assigned")
    return result


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:60] or "inventory"


def make_inventory(name, result, *, source_filename="", id=None):
    return Inventory(id=id or slugify(name), name=name, created_at=time.time(), source_filename=source_filename,
                     rows=[Row.from_json(r.to_json()) for r in result.rows], unmapped_parts=list(result.unmapped_parts),
                     unmapped_colours=list(result.unmapped_colours), warnings=list(result.warnings),
                     review=[dict(r) for r in result.review], origin=result.origin)


# --------------------------------------------------------------------- queries

def combine(inventories):
    total = Counter()
    for inv in inventories:
        for r in inv.rows:
            total[(r.part, r.colour)] += r.qty
    return dict(total)


def colour_name(code, library=None):
    if library is not None:
        colour = library.colours_by_code.get(code)
        if colour is not None and colour.name:
            return colour.name.replace("_", " ")
    for entry in load_maps()["colours"].values():
        if entry.get("ldraw") == code and entry.get("name"):
            return entry["name"]
    return f"Colour {code}"


def describe(part, library=None):
    return (library.by_code.get(part) or "") if library is not None else ""


def summary(inventories, limit=40, library=None):
    owned = combine(inventories)
    by_part, by_colour = Counter(), Counter()
    for (p, c), q in owned.items():
        by_part[p] += q
        by_colour[c] += q
    ranked = lambda counter: sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]  # noqa: E731
    return dict(inventories=[i.name for i in inventories], distinct_parts=len(by_part), distinct_colours=len(by_colour),
                distinct_part_colours=len(owned), total_pieces=sum(owned.values()),
                top_parts=[dict(part=p, qty=q, description=describe(p, library)) for p, q in ranked(by_part)],
                top_colours=[dict(colour=c, name=colour_name(c, library), qty=q) for c, q in ranked(by_colour)])


def search_owned(inventories, query="", *, colour=None, min_qty=1, limit=50, library=None):
    tokens = query.casefold().split()
    hits = []
    for (part, c), qty in combine(inventories).items():
        if qty < min_qty or (colour is not None and c != colour):
            continue
        description = describe(part, library)
        haystack = f"{part} {description}".casefold()
        if all(t in haystack for t in tokens):
            hits.append(dict(part=part, colour=c, colour_name=colour_name(c, library), qty=qty, description=description))
    hits.sort(key=lambda h: (-h["qty"], h["part"], h["colour"]))
    return hits[:limit]


# ----------------------------------------------------------------- model check

def model_bom(model, library):
    """Physical BOM {(part, colour): qty}, exactly as `ldraw-agent bom` counts it."""
    from .common import normalized
    from .document import physical_context
    view, overlay = physical_context(model, library)
    needed = Counter()
    for row in view.bill_of_materials(parts=overlay):
        part = normalized(row.part).removesuffix(".dat").rsplit("/", 1)[-1]
        needed[(part, row.colour_code)] += row.quantity
    return needed


def index_owned(owned):
    """{part: [(colour, qty)]} of a combined inventory, for the substitution and not-owned lookups."""
    by_part = defaultdict(list)
    for (p, c), q in owned.items():
        by_part[p].append((c, q))
    return by_part


def match_bom(needed, owned):
    """The (part, colour) comparison every model check builds on: (matched pieces, short lines).

    A line is (part, colour, needed, owned) for each (part, colour) the inventory holds too few of,
    sorted by part and colour.  Pure counting, no names or library: cheap enough for a whole model catalogue.
    """
    ok, short = 0, []
    for (part, col), need in sorted(needed.items(), key=lambda kv: (kv[0][0], kv[0][1] is not None, kv[0][1] or 0)):  # colour None: unresolved
        have = owned.get((part, col), 0)
        ok += min(need, have)
        if have < need:
            short.append((part, col, need, have))
    return ok, short


def review_pool(inventories):
    """[(part, candidates, qty)] of the colour-family (review) items of the inventories: the "maybe" pieces, never in `owned`."""
    return [(str(r["part"]).casefold().removesuffix(".dat"), frozenset(r.get("candidates", ())), int(r["qty"]))
            for inv in inventories for r in inv.review if int(r.get("qty", 0)) > 0]


def maybe_pieces(short, pool):
    """Extra pieces the review items could cover for the short lines of `match_bom`, each review piece used at most once.

    A line (part, colour, needed, owned) can take review pieces of the same part whose family lists that colour as a candidate.
    Exact counts are never changed; this is the separate "maybe" number.
    """
    left = [[part, cands, qty] for part, cands, qty in pool]
    total = 0
    for part, col, need, have in short:
        gap = need - have
        for item in left:
            if gap <= 0:
                break
            if item[0] == part and col in item[1] and item[2] > 0:
                take = min(gap, item[2])
                item[2] -= take
                gap -= take
                total += take
    return total


def substituted_pieces(needed, by_part):
    """Matched pieces when any owned colour of a part may stand in for a missing one: sum over parts of min(needed, owned)."""
    need_by_part = Counter()
    for (part, _), need in needed.items():
        need_by_part[part] += need
    return sum(min(n, sum(q for _, q in by_part.get(part, ()))) for part, n in need_by_part.items())


def check_model(model, inventories, *, mode="prefer", library=None, substitutions=True, needed=None):
    """Compare a model's physical BOM with inventories; `needed` skips the BOM expansion when it is already known."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    if library is None:
        raise ValueError("check_model needs the parts library to expand the model's physical BOM")
    if needed is None:
        needed = model_bom(model, library)
    owned = combine(inventories)
    by_part = index_owned(owned)
    ok, lines = match_bom(needed, owned)
    short, not_owned, subs = [], [], []
    for part, col, need, have in lines:
        short.append(dict(part=part, colour=col, colour_name=colour_name(col, library), needed=need, owned=have, missing=need - have))
        if part not in by_part:
            if not any(n["part"] == part for n in not_owned):
                not_owned.append(dict(part=part, description=describe(part, library)))
        elif substitutions:
            others = sorted(((c, q) for c, q in by_part[part] if c != col), key=lambda cq: (-cq[1], cq[0]))
            if others:
                subs.append(dict(part=part, wanted_colour=col, owned_colours=[dict(colour=c, qty=q) for c, q in others]))
    needed_total, missing = sum(needed.values()), sum(s["missing"] for s in short)
    text = (f"All {needed_total} pieces are covered by the inventory." if not short else
            f"{ok} of {needed_total} pieces owned; short by {missing} piece(s) across {len(short)} part/colour line(s), "
            f"{len(not_owned)} part(s) not owned at all.")
    return dict(mode=mode, complete=not short, needed_total=needed_total, owned_total=sum(owned.values()), short=short,
                not_owned_parts=not_owned, colour_substitutions=subs, ok_pieces=ok, summary=text)
