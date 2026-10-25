"""Storage boundary of the parts collection: the ONLY module the collection feature uses to read or write its data.

Today it is JSON files in INVENTORIES_DIR (`<id>.json` source, `<id>.csv` original upload, `<id>.log.jsonl` change log,
`.collection-notes.json` + `.collection-notes.log.jsonl` part notes). Everything above (collection.py, the routers) calls
only the plain functions below and passes plain dicts/lists, so moving to PostgreSQL later means rewriting this module
and nothing else. Callers hold `lock` around read-modify-write sequences (a DB version would use a transaction instead).

The toolkit CLI runs in the sandbox and cannot call these functions, so it is handed the store through the "CLI bridge"
below (`cli_store_args`, `cli_environment`, `scratch_dir`): backend code never builds a path into the folder itself.
Older read-only consumers (workspace.owned_parts, parts_steering.load_inventory) still read the source files directly; they
only look at exact `rows` and must follow the same move.
"""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any

import inventories
import settings
from inventories import atomic_write

lock = inventories._lock                      # one lock for check-then-write sequences
NOTES_FILE = ".collection-notes.json"         # dot-prefixed: never an inventory id, never listed as a source
NOTES_LOG = ".collection-notes.log.jsonl"
RESERVATIONS_FILE = ".collection-reservations.json"   # also read by the toolkit CLI (`inventory buildable --available`)
MAX_BATCHES = 500                             # history kept per source


def _dump(data: Any) -> bytes:
    return (json.dumps(data, indent=2) + "\n").encode()


# --- sources -------------------------------------------------------------------

def exists(source_id: str) -> bool:
    return inventories.exists(source_id)


def load(source_id: str) -> dict:
    """The stored source, shared and cached: never modify it (InventoryError 404 when missing)."""
    return inventories._load(source_id)


def load_copy(source_id: str) -> dict:
    return copy.deepcopy(load(source_id))


def save(data: dict) -> None:
    atomic_write(inventories._path(data["id"]), _dump(data))


def source_ids() -> list[str]:
    folder = settings.INVENTORIES_DIR
    return [p.stem for p in sorted(folder.glob("*.json")) if inventories.valid_id(p.stem)] if folder.is_dir() else []


# --- the original upload ---------------------------------------------------------

def read_csv(source_id: str, max_bytes: int | None = None) -> str | None:
    path = inventories._path(source_id, "csv")
    if not path.is_file() or (max_bytes is not None and path.stat().st_size > max_bytes):
        return None
    return path.read_text(encoding="utf-8")


def write_csv(source_id: str, raw: bytes) -> None:
    atomic_write(inventories._path(source_id, "csv"), raw)


# --- change log (append-only, one line per change, grouped in batches) -------------

def _log_path(source_id: str):
    return settings.INVENTORIES_DIR / f"{source_id}.log.jsonl"


def read_log(source_id: str) -> list[dict]:
    try:
        text = _log_path(source_id).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue                # a torn last line must not lose the rest of the history
    return out


def append_log(source_id: str, new: list[dict]) -> None:
    """Appends entries; keeps only the last MAX_BATCHES batches."""
    entries = read_log(source_id) + new
    batches = sorted({e["batch"] for e in entries})
    path = _log_path(source_id)
    if len(batches) > MAX_BATCHES:
        keep = set(batches[-MAX_BATCHES:])
        atomic_write(path, "".join(json.dumps(e) + "\n" for e in entries if e["batch"] in keep).encode())
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write("".join(json.dumps(e) + "\n" for e in new))
    path.chmod(0o644)


# --- part notes ------------------------------------------------------------------

def read_notes() -> dict[str, str]:
    try:
        data = json.loads((settings.INVENTORIES_DIR / NOTES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    notes = data.get("notes") if isinstance(data, dict) else None
    return {k: v for k, v in notes.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(notes, dict) else {}


def write_notes(notes: dict[str, str], log_entry: dict) -> None:
    atomic_write(settings.INVENTORIES_DIR / NOTES_FILE, _dump({"schema": 1, "notes": dict(sorted(notes.items()))}))
    with (settings.INVENTORIES_DIR / NOTES_LOG).open("a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")


# --- reservations (parts held by models the owner built) ---------------------------

def read_reservations() -> list[dict]:
    try:
        data = json.loads((settings.INVENTORIES_DIR / RESERVATIONS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = data.get("reservations") if isinstance(data, dict) else None
    return [r for r in found if isinstance(r, dict) and isinstance(r.get("rows"), list)] if isinstance(found, list) else []


def write_reservations(reservations: list[dict]) -> None:
    atomic_write(settings.INVENTORIES_DIR / RESERVATIONS_FILE, _dump({"schema": 1, "reservations": reservations}))


def reservations_signature() -> str:
    try:
        stat = (settings.INVENTORIES_DIR / RESERVATIONS_FILE).stat()
    except OSError:
        return ""
    return f"{stat.st_mtime_ns}:{stat.st_size}"


# --- remembered column mappings of generic CSV imports ------------------------------

MAPPINGS_FILE = ".collection-import-mappings.json"      # dot-prefixed: never an inventory id


def read_import_mappings() -> dict[str, dict[str, str]]:
    """{header signature: {part, colour, qty, system}} the owner confirmed before."""
    try:
        data = json.loads((settings.INVENTORIES_DIR / MAPPINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found = data.get("mappings") if isinstance(data, dict) else None
    if not isinstance(found, dict):
        return {}
    return {k: v for k, v in found.items() if isinstance(k, str) and isinstance(v, dict) and all(isinstance(x, str) for x in v.values())}


def write_import_mapping(signature: str, mapping: dict[str, str]) -> None:
    mappings = read_import_mappings()
    mappings[signature] = mapping
    for old in list(mappings)[:-50]:                    # a spreadsheet habit is a handful of layouts, not hundreds
        del mappings[old]
    atomic_write(settings.INVENTORIES_DIR / MAPPINGS_FILE, _dump({"schema": 1, "mappings": mappings}))


# --- CLI bridge: how the sandboxed toolkit finds the store ---------------------------

def cli_store_args() -> list[str]:
    """The `--store` option of the `inventory ...` CLI commands (a DB version would export a snapshot and point at it)."""
    return ["--store", str(settings.INVENTORIES_DIR)]


def cli_environment() -> dict[str, str]:
    """Environment variables the sandboxed toolkit reads to find the store."""
    return {"LDRAW_NOVA_INVENTORY_DIR": str(settings.INVENTORIES_DIR)}


def scratch_dir(prefix: str) -> Path:
    """A fresh private working folder next to the store (dot-prefixed, so never listed as a source); the caller removes it."""
    settings.INVENTORIES_DIR.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=settings.INVENTORIES_DIR))


# --- exact-row readers for the features that count what is owned -------------------
# (workspace parts check, editor palette via parts_steering, chat steering). Exact `rows` only: review ("maybe") pieces
# never appear here, so nothing downstream can count them.

MAX_FILE_BYTES = 64 * 1024 * 1024


def exact_rows(source_id: str) -> tuple[str, list[tuple[str, int, int]]] | None:
    """(name, [(part, colour, qty)]) of one source's exact rows, or None when it is missing, unreadable or too big."""
    if not inventories.valid_id(source_id):
        return None
    try:
        if inventories._path(source_id).stat().st_size > MAX_FILE_BYTES:
            return None
        data = load(source_id)
        rows = [(str(r["part"]), int(r["colour"]), int(r["qty"])) for r in data.get("rows", []) if int(r["qty"]) > 0]
        return str(data.get("name") or source_id), rows
    except (OSError, ValueError, KeyError, TypeError, AttributeError, inventories.InventoryError):
        return None


def owned_totals() -> tuple[dict[tuple[str, int], int], str]:
    """Every source added together, exact rows only: ({(part, colour): qty}, signature that changes when any source does)."""
    owned: dict[tuple[str, int], int] = {}
    signature = []
    for sid in source_ids():
        try:
            stat = inventories._path(sid).stat()
            data = load(sid)
        except (OSError, inventories.InventoryError):
            continue
        signature.append(f"{sid}:{stat.st_mtime_ns}:{stat.st_size}")
        for row in data.get("rows", []):
            key = (str(row["part"]).casefold().removesuffix(".dat"), row["colour"])
            owned[key] = owned.get(key, 0) + int(row["qty"])
    return owned, "|".join(signature)
