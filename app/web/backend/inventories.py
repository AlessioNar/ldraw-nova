"""The user's parts inventories: storage, import through the toolkit CLI, queries and per-chat options.

The backend's system Python cannot import ldraw_tools (pyldraw3 and numpy live in the
toolkit's own uv environment), so parsing and mapping a Rebrickable CSV run as a
`ldraw_tools.cli inventory import` subprocess. Everything else reads and writes the
stored files directly. The format is the toolkit's `Inventory.to_json` (rows of
{part, colour, qty, rb_part, rb_colour}) plus an "import" block with what the parser
reported about the upload (dialect, rows read, spare pieces).

Storage: <INVENTORIES_DIR>/<id>.json and <id>.csv (the original upload). The folder is
readable by the agent user: toolkit calls in the sandbox read the same files.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any

import fsutil
import sandbox
import settings
import toolkit
from fsutil import atomic_write  # noqa: F401  (kept importable from here)

log = logging.getLogger(__name__)

MAX_CSV_BYTES = 25 * 1024 * 1024            # the toolkit parser's own limit
MAX_NAME_CHARS = 80
MAX_IDS = 20
IMPORT_TIMEOUT = 180
IMPORT_FORMATS = ("rebrickable-csv", "brickit-pileometer", "bricklink-xml", "brickstore-bsx", "bricklink-csv", "generic-csv")
COLOUR_SYSTEMS = ("bricklink", "rebrickable", "ldraw", "name")
MAPPING_ROLES = ("part", "colour", "qty", "system")
MAX_REPORT_BYTES = 256 * 1024 * 1024
PARTS_MODES = ("off", "prefer", "only")
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,59}")
LOOSE_ID = "loose"                          # the built-in editable source for manual and quick adds (collection.py)
RESERVED_IDS = ("buildable", LOOSE_ID)      # /api/inventories/buildable is a route; "loose" is created by the collection

_lock = fsutil.lock                         # check-then-write of ids and names
_cache: OrderedDict[tuple, dict] = OrderedDict()


class InventoryError(Exception):
    """A request problem; `status` is the HTTP status the router answers with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class NeedsMapping(InventoryError):
    """A CSV no importer recognises: `info` holds its headers, a few rows and a guess at the columns."""

    def __init__(self, message: str, info: dict):
        super().__init__(422, message)
        self.info = info


# --- per-chat options --------------------------------------------------------

def parts_options(options: dict | None) -> tuple[str, list[str]]:
    """(mode, inventory ids) of validated turn options. The ids are [] when the mode is "off"."""
    options = options or {}
    mode = options.get("parts_mode")
    if mode not in ("prefer", "only"):
        return "off", []
    ids = options.get("inventory_ids")
    return mode, [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []


def validate_parts_options(value: dict) -> None:
    """Checks and normalises parts_mode / inventory_ids of a turn's options in place; raises ValueError."""
    mode, ids = value.get("parts_mode", "off"), value.get("inventory_ids", [])
    if mode is None:
        mode = "off"
    if mode not in PARTS_MODES:
        raise ValueError("Unknown parts mode (expected off, prefer or only)")
    if ids is None:
        ids = []
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ValueError("inventory_ids must be a list of inventory ids")
    ids = list(dict.fromkeys(ids))
    if len(ids) > MAX_IDS:
        raise ValueError(f"Choose at most {MAX_IDS} inventories")
    bad = [i for i in ids if not valid_id(i)]
    if bad:
        raise ValueError(f"Invalid inventory id {bad[0][:60]!r}")
    if mode == "off":
        ids = [i for i in ids if exists(i)]     # a deleted inventory is only a problem when it is needed
    else:
        if not ids:
            raise ValueError(f"Choose at least one inventory to use parts mode {mode}")
        missing = [i for i in ids if not exists(i)]
        if missing:
            raise ValueError(f"Inventory {missing[0]!r} no longer exists; choose another one")
    value["parts_mode"], value["inventory_ids"] = mode, ids


# --- storage -----------------------------------------------------------------

def valid_id(inventory_id: Any) -> bool:
    return isinstance(inventory_id, str) and ID_PATTERN.fullmatch(inventory_id) is not None


def slugify(name: str) -> str:
    """Identical to ldraw_tools.inventory.slugify: the CLI names the stored files with it."""
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:60] or "inventory"


def _path(inventory_id: str, ext: str = "json") -> Path:
    if not valid_id(inventory_id):
        raise InventoryError(404, "no such inventory")
    return settings.INVENTORIES_DIR / f"{inventory_id}.{ext}"


def _store():
    """The collection store (files or PostgreSQL); imported late because the store's file backend imports this module."""
    import collection_store
    return collection_store


def exists(inventory_id: str) -> bool:
    return valid_id(inventory_id) and _store().exists(inventory_id)


def _load(inventory_id: str) -> dict:
    path = _path(inventory_id)
    try:
        stat = path.stat()
    except FileNotFoundError:
        raise InventoryError(404, "no such inventory") from None
    key = (inventory_id, stat.st_mtime_ns, stat.st_size)
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InventoryError(404, "no such inventory") from None
    except (OSError, ValueError) as exc:
        raise InventoryError(500, f"Inventory {inventory_id} is unreadable: {exc}") from None
    for old in [k for k in _cache if k[0] == inventory_id]:
        del _cache[old]
    _cache[key] = data
    while len(_cache) > 4:
        _cache.popitem(last=False)
    return data


def summary(data: dict) -> dict:
    """List-card facts of one source. Counts cover exact rows only; colour-family pieces (`review`) are counted apart."""
    rows = data.get("rows", [])
    review = data.get("review", [])         # colour-family pieces not counted as owned (Pileometer); keys only when present
    extra = {"review_items": len(review), "review_pieces": sum(r["qty"] for r in review)} if review else {}
    set_info = data.get("set")
    if isinstance(set_info, dict):
        set_info = {**set_info, "brand": set_info.get("brand") or "LEGO", "image": set_info.get("image") or None}
    return {
        **extra,
        "kind": data.get("kind") or "import", "schema": data.get("schema", 1),
        **({"set": set_info} if isinstance(set_info, dict) else {}),
        **({"origin": data["origin"]} if isinstance(data.get("origin"), dict) else {}),
        "id": data["id"], "name": data.get("name", data["id"]), "created_at": data.get("created_at", 0.0),
        "source_filename": data.get("source_filename") or None,
        "distinct_parts": len({r["part"] for r in rows}), "distinct_colours": len({r["colour"] for r in rows}),
        "total_pieces": sum(r["qty"] for r in rows), "spare_pieces": int(data.get("import", {}).get("spare_pieces", 0)),
        "unmapped_parts": len(data.get("unmapped_parts", [])), "unmapped_colours": len(data.get("unmapped_colours", [])),
    }


def list_inventories() -> list[dict]:
    store = _store()
    found = []
    for source_id in store.source_ids():
        try:
            found.append(summary(store.load(source_id)))
        except InventoryError as exc:
            log.warning("skipping inventory %s: %s", source_id, exc)
    return sorted(found, key=lambda s: (-s["created_at"], s["id"]))


def _name_taken(name: str, exclude: str = "") -> bool:
    """Same slug as another inventory's id or display name."""
    slug = slugify(name)
    for item in list_inventories():
        if item["id"] != exclude and slug in (item["id"], slugify(item["name"])):
            return True
    return False


def clean_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise InventoryError(400, "Give the inventory a name")
    name = name.strip()
    if len(name) > MAX_NAME_CHARS:
        raise InventoryError(400, f"The name is longer than {MAX_NAME_CHARS} characters")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise InventoryError(400, "The name contains control characters")
    return name


def rename(inventory_id: str, name: str) -> dict:
    name = clean_name(name)
    with _lock:
        data = dict(_store().load(inventory_id))
        if _name_taken(name, exclude=inventory_id):
            raise InventoryError(409, f"An inventory called {name!r} already exists")
        data["name"] = name
        _store().save(data)
    return summary(data)


def delete(inventory_id: str) -> None:
    with _lock:
        if not valid_id(inventory_id):
            raise InventoryError(404, "no such inventory")
        _store().delete(inventory_id)


# --- LDraw library names -----------------------------------------------------

@lru_cache(maxsize=1)
def _descriptions() -> dict[str, str]:
    try:
        import ldraw
        return {pid.removesuffix(".dat"): description for pid, description, *_ in ldraw.parts_index()}
    except Exception as exc:                    # no library or index: descriptions are optional
        log.warning("part descriptions unavailable: %s", exc)
        return {}


@lru_cache(maxsize=1)
def _colour_names() -> dict[int, str]:
    try:
        import ldraw
        return {code: name.replace("_", " ") for code, name in ldraw.colours().items()}
    except Exception as exc:
        log.warning("colour names unavailable: %s", exc)
        return {}


def clear_caches() -> None:
    _descriptions.cache_clear()
    _colour_names.cache_clear()
    _cache.clear()


def detail(inventory_id: str, query: str = "", colour: int | None = None, limit: int = 50, offset: int = 0) -> dict:
    data = _store().load(inventory_id)
    descriptions, names = _descriptions(), _colour_names()
    rows = data.get("rows", [])
    totals: dict[int, int] = {}
    for r in rows:
        totals[r["colour"]] = totals.get(r["colour"], 0) + r["qty"]
    top = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))[:12]
    tokens = query.casefold().split()
    hits = []
    for r in rows:
        if colour is not None and r["colour"] != colour:
            continue
        description = descriptions.get(r["part"], "")
        if tokens:
            haystack = f"{r['part']} {description}".casefold()
            if not all(t in haystack for t in tokens):
                continue
        hits.append((r, description))
    hits.sort(key=lambda h: (-h[0]["qty"], h[0]["part"], h[0]["colour"]))
    page = hits[offset:offset + limit]
    return {
        "inventory": summary(data),
        "rows": [{"part": r["part"], "colour": r["colour"], "colour_name": names.get(r["colour"], ""),
                  "qty": r["qty"], "description": d} for r, d in page],
        "total": len(hits),
        "top_colours": [{"colour": c, "colour_name": names.get(c, ""), "qty": q} for c, q in top],
    }


# --- import ------------------------------------------------------------------

def clean_family_colours(value: Any) -> dict[str, int]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict) or len(value) > 20 or not all(isinstance(k, str) and re.fullmatch(r"[A-Za-z]{1,20}", k) for k in value):
        raise InventoryError(400, "family_colours must map colour family names to LDraw colour codes")
    if not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 9999 for v in value.values()):
        raise InventoryError(400, "family_colours must map colour family names to LDraw colour codes")
    return dict(value)


def remap_families(inventory_id: str) -> tuple[str, str]:
    """(name, stored CSV text) of an inventory whose colour families are being mapped again."""
    data = _store().load(inventory_id)
    csv_text = _store().read_csv(inventory_id)
    if (data.get("import") or {}).get("dialect") != "pileometer" or csv_text is None:
        raise InventoryError(400, "Only a Pileometer import has colour families")
    return data.get("name", inventory_id), csv_text


def clean_mapping(mapping: dict | None) -> dict[str, str]:
    """The owner's column choice for a generic CSV: part, colour, qty (header names) and system (how colours are written)."""
    if not mapping:
        return {}
    if not isinstance(mapping, dict):
        raise InventoryError(400, "mapping must be an object")
    out = {}
    for role, value in mapping.items():
        if role not in MAPPING_ROLES or not isinstance(value, str) or not value or len(value) > 200 or "\0" in value:
            raise InventoryError(400, f"mapping: {role!r} must be one of {', '.join(MAPPING_ROLES)} with a text value")
        out[role] = value
    if out.get("system", "name") not in COLOUR_SYSTEMS:
        raise InventoryError(400, f"mapping: system must be one of {', '.join(COLOUR_SYSTEMS)}")
    return out


async def parse_csv(name: str, csv_text: str, include_spares: bool = False, family_colours: dict | None = None,
                    replace: str = "", import_format: str = "", mapping: dict | None = None) -> dict:
    """Parses an upload with the toolkit CLI (`inventory import --dry-run`) and stores nothing.

    Returns {"id", "data": the inventory dict, "report": {...what the UI shows...}, "raw": the CSV bytes}. Storing, the
    name check and the history are collection.py's job, so every import can be previewed before it is committed.
    `replace` is the id of an inventory whose id the result takes (same name), used to map colour families again.
    """
    name = clean_name(name)
    family_colours = clean_family_colours(family_colours)
    mapping = clean_mapping(mapping)
    if import_format and import_format not in IMPORT_FORMATS:
        raise InventoryError(400, f"Unknown import format {import_format!r}")
    if not isinstance(csv_text, str):
        raise InventoryError(400, "csv must be text")
    raw = csv_text.encode("utf-8")
    if len(raw) > MAX_CSV_BYTES:
        raise InventoryError(413, f"File exceeds the {MAX_CSV_BYTES // (1024 * 1024)} MB limit")
    inventory_id = replace or slugify(name)      # a replaced inventory keeps its id even after a rename
    work = _store().scratch_dir(".import-")
    try:
        work.chmod(0o755)
        (work / "upload.csv").write_bytes(raw)
        sandbox.give_to_agent(work)
        sandbox.give_to_agent(work / "upload.csv")
        # The report is the CLI's stdout, redirected to a file: it carries every row, far past the
        # sandbox's output cap. Arguments follow the script as positional parameters, never the shell text.
        argv = ["bash", "-c", 'exec "$@" > report.json', "import", str(settings.TOOLKIT_PYTHON), "-m", "ldraw_tools.cli",
                "inventory", "import", "upload.csv", f"--name={replace or name}", "--dry-run", "--json",
                *(["--include-spares"] if include_spares else []),
                *([f"--format={import_format}"] if import_format else []),
                *(f"--map={role}={value}" for role, value in mapping.items()),
                *(f"--family-colour={family}={code}" for family, code in family_colours.items())]
        result = await sandbox.run(argv, work, timeout=IMPORT_TIMEOUT, env_extra=toolkit.environment())
        if result.timed_out:
            raise InventoryError(504, "Importing the file took too long")
        report_path = work / "report.json"
        try:
            if report_path.stat().st_size > MAX_REPORT_BYTES:
                raise ValueError("report too large")
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.error("inventory import failed (exit %s): %s %s", result.exit_code, exc, result.stderr[-2000:])
            raise InventoryError(500, "The toolkit could not import the file") from None
        if result.exit_code != 0 or "inventory" not in report:
            if report.get("error_type") == "NeedsMapping" and isinstance(report.get("mapping_needed"), dict):
                raise NeedsMapping(str(report.get("error", "Choose the columns")), report["mapping_needed"])
            if report.get("error_type") == "ParseError":
                raise InventoryError(400, str(report.get("error", "Not a valid inventory CSV")))
            log.error("inventory import failed (exit %s): %s %s", result.exit_code, report.get("error"), result.stderr[-2000:])
            raise InventoryError(500, "The toolkit could not import the file")
        data = report["inventory"]
        data["name"] = name
        if data.get("id") != inventory_id:
            log.error("toolkit named the inventory %r, expected %r", data.get("id"), inventory_id)
            raise InventoryError(500, "The toolkit could not import the file")
        data["source_filename"] = f"{inventory_id}.csv"
        data["import"] = {key: report.get(key, 0 if key != "dialect" else "")
                          for key in ("dialect", "total_rows", "total_pieces", "spare_pieces")}
        if report.get("pileometer"):
            data["import"]["pileometer"] = report["pileometer"]
        out_report = {
            "dialect": report.get("dialect", ""), "format": report.get("format", ""), "total_rows": report.get("total_rows", 0),
            "total_pieces": report.get("total_pieces", 0), "spare_pieces": report.get("spare_pieces", 0),
            "unmapped_parts": data.get("unmapped_parts", []), "unmapped_colours": data.get("unmapped_colours", []),
            "warnings": report.get("warnings", []),
            **({"pileometer": report["pileometer"]} if report.get("pileometer") else {}),
            **({"mapping": report["mapping"], "header_signature": report.get("header_signature", "")} if report.get("mapping") else {}),
        }
        return {"id": inventory_id, "data": data, "report": out_report, "raw": raw}
    finally:
        await asyncio.to_thread(shutil.rmtree, work, True)
