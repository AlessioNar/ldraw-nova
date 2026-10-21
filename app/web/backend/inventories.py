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
import tempfile
import threading
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any

import sandbox
import settings
import toolkit

log = logging.getLogger(__name__)

MAX_CSV_BYTES = 25 * 1024 * 1024            # the toolkit parser's own limit
MAX_NAME_CHARS = 80
MAX_IDS = 20
IMPORT_TIMEOUT = 180
MAX_REPORT_BYTES = 256 * 1024 * 1024
PARTS_MODES = ("off", "prefer", "only")
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,59}")

_lock = threading.Lock()                    # check-then-write of ids and names
_cache: OrderedDict[tuple, dict] = OrderedDict()


class InventoryError(Exception):
    """A request problem; `status` is the HTTP status the router answers with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


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


def exists(inventory_id: str) -> bool:
    return valid_id(inventory_id) and _path(inventory_id).is_file()


def atomic_write(path: Path, data: bytes) -> None:
    """Temp file in the same folder, then rename: readers never see a partial inventory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".", suffix=".tmp", delete=False) as f:
            tmp = Path(f.name)
            f.write(data)
        tmp.chmod(0o644)
        tmp.replace(path)
        tmp = None
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


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
    rows = data.get("rows", [])
    return {
        "id": data["id"], "name": data.get("name", data["id"]), "created_at": data.get("created_at", 0.0),
        "source_filename": data.get("source_filename") or None,
        "distinct_parts": len({r["part"] for r in rows}), "distinct_colours": len({r["colour"] for r in rows}),
        "total_pieces": sum(r["qty"] for r in rows), "spare_pieces": int(data.get("import", {}).get("spare_pieces", 0)),
        "unmapped_parts": len(data.get("unmapped_parts", [])), "unmapped_colours": len(data.get("unmapped_colours", [])),
    }


def list_inventories() -> list[dict]:
    folder = settings.INVENTORIES_DIR
    found = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if not valid_id(path.stem):
            continue
        try:
            found.append(summary(_load(path.stem)))
        except InventoryError as exc:
            log.warning("skipping inventory %s: %s", path.name, exc)
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
        data = dict(_load(inventory_id))
        if _name_taken(name, exclude=inventory_id):
            raise InventoryError(409, f"An inventory called {name!r} already exists")
        data["name"] = name
        atomic_write(_path(inventory_id), (json.dumps(data, indent=2) + "\n").encode())
    return summary(data)


def delete(inventory_id: str) -> None:
    with _lock:
        path = _path(inventory_id)
        if not path.is_file():
            raise InventoryError(404, "no such inventory")
        path.unlink()
        _path(inventory_id, "csv").unlink(missing_ok=True)


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
    data = _load(inventory_id)
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

async def import_csv(name: str, csv_text: str, include_spares: bool = False) -> dict:
    """Parses and stores an upload with the toolkit CLI; returns {"inventory": summary, "report": {...}}."""
    name = clean_name(name)
    if not isinstance(csv_text, str):
        raise InventoryError(400, "csv must be text")
    raw = csv_text.encode("utf-8")
    if len(raw) > MAX_CSV_BYTES:
        raise InventoryError(413, f"File exceeds the {MAX_CSV_BYTES // (1024 * 1024)} MB limit")
    inventory_id = slugify(name)
    if _name_taken(name):
        raise InventoryError(409, f"An inventory called {name!r} already exists")
    folder = settings.INVENTORIES_DIR
    folder.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".import-", dir=folder))
    try:
        work.chmod(0o755)
        (work / "upload.csv").write_bytes(raw)
        sandbox.give_to_agent(work)
        sandbox.give_to_agent(work / "upload.csv")
        # The report is the CLI's stdout, redirected to a file: it carries every row, far past the
        # sandbox's output cap. Arguments follow the script as positional parameters, never the shell text.
        argv = ["bash", "-c", 'exec "$@" > report.json', "import", str(settings.TOOLKIT_PYTHON), "-m", "ldraw_tools.cli",
                "inventory", "import", "upload.csv", f"--name={name}", "--store", "store", "--json",
                *(["--include-spares"] if include_spares else [])]
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
            if report.get("error_type") == "ParseError":
                raise InventoryError(400, str(report.get("error", "Not a valid inventory CSV")))
            log.error("inventory import failed (exit %s): %s %s", result.exit_code, report.get("error"), result.stderr[-2000:])
            raise InventoryError(500, "The toolkit could not import the file")
        data = report["inventory"]
        if data.get("id") != inventory_id:
            log.error("toolkit named the inventory %r, expected %r", data.get("id"), inventory_id)
            raise InventoryError(500, "The toolkit could not import the file")
        data["source_filename"] = f"{inventory_id}.csv"
        data["import"] = {key: report.get(key, 0 if key != "dialect" else "")
                          for key in ("dialect", "total_rows", "total_pieces", "spare_pieces")}
        with _lock:
            if _name_taken(name):
                raise InventoryError(409, f"An inventory called {name!r} already exists")
            atomic_write(_path(inventory_id, "csv"), raw)
            atomic_write(_path(inventory_id), (json.dumps(data, indent=2) + "\n").encode())
        return {"inventory": summary(data), "report": {
            "dialect": report.get("dialect", ""), "total_rows": report.get("total_rows", 0),
            "total_pieces": report.get("total_pieces", 0), "spare_pieces": report.get("spare_pieces", 0),
            "unmapped_parts": data.get("unmapped_parts", []), "unmapped_colours": data.get("unmapped_colours", []),
            "warnings": report.get("warnings", []),
        }}
    finally:
        await asyncio.to_thread(shutil.rmtree, work, True)
