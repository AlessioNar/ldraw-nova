"""Storage boundary of the parts collection: the ONLY module the collection feature uses to read or write its data.

Two interchangeable backends behind the same functions, chosen once per process like the chats and builds:

* collection_files.py   JSON files in INVENTORIES_DIR (`<id>.json`, `<id>.csv`, `<id>.log.jsonl`, notes, reservations)
* collection_pg.py      PostgreSQL (part_source, part_row, part_review, part_log, part_note, part_reservation, ...); after
                        every commit it keeps a derived JSON copy of the collection in COLLECTION_EXPORT_DIR, which is what
                        the sandboxed toolkit CLI reads through `--store` (the agent never reaches the database)

PostgreSQL is used when storage.use_database() says so AND the collection is in it: LDRAW_NOVA_STORAGE=postgres always,
`auto` once `python -m migrate_json --collection --commit` recorded app_setting['collection_import'] (or when there are
no collection files to import). Until then `auto` keeps the collection on its files, the safe way to cut over.

Callers hold `lock` around read-modify-write sequences and pass plain dicts/lists.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

import fsutil
import settings

log = logging.getLogger("collection_store")

lock = fsutil.lock
NOTES_FILE = ".collection-notes.json"         # dot-prefixed: never an inventory id, never listed as a source
NOTES_LOG = ".collection-notes.log.jsonl"
RESERVATIONS_FILE = ".collection-reservations.json"   # also read by the toolkit CLI (`inventory buildable --available`)
MAPPINGS_FILE = ".collection-import-mappings.json"
MAX_BATCHES = 500                             # history kept per source
MAX_FILE_BYTES = 64 * 1024 * 1024

_decision: Optional[bool] = None
_forced: Optional[bool] = None                # tests: True = PostgreSQL, False = files


def import_done() -> bool:
    import db
    from sqlalchemy import text
    try:
        with db.get_engine().connect() as connection:
            value = connection.execute(text("select value from app_setting where key = 'collection_import'")).scalar()
        return bool(isinstance(value, dict) and value.get("done"))
    except Exception:  # noqa: BLE001
        return False


def files_present() -> bool:
    """Source files exist in INVENTORIES_DIR (so an import is pending unless it was done)."""
    import inventories
    folder = settings.INVENTORIES_DIR
    try:
        return folder.is_dir() and any(inventories.valid_id(p.stem) for p in folder.glob("*.json"))
    except OSError:
        return False


def use_database() -> bool:
    global _decision
    if _forced is not None:
        return _forced
    if _decision is None:
        import storage
        if not storage.use_database():
            _decision = False
        elif storage.mode() == "postgres":
            _decision = True
        else:
            _decision = import_done() or not files_present()
        log.info("parts collection: %s", "PostgreSQL" if _decision else "JSON files")
    return _decision


def refresh(force: Optional[bool] = None) -> bool:
    """Forget the decision (after storage.refresh(), or in tests with `force`: True = PostgreSQL, False = files)."""
    global _decision, _forced
    _decision, _forced = None, force
    return use_database()


def _b():
    if use_database():
        import collection_pg
        return collection_pg
    import collection_files
    return collection_files


def start() -> None:
    """At app start: PostgreSQL mode regenerates the derived JSON copy for the CLI."""
    if use_database():
        _b().sync_export()


# --- sources -------------------------------------------------------------------

def exists(source_id: str) -> bool:
    return _b().exists(source_id)


def load(source_id: str) -> dict:
    """The stored source, shared and cached: never modify it (InventoryError 404 when missing)."""
    return _b().load(source_id)


def load_copy(source_id: str) -> dict:
    return _b().load_copy(source_id)


def save(data: dict) -> None:
    _b().save(data)


def delete(source_id: str) -> None:
    """Removes a source with its upload and change log (InventoryError 404 when missing)."""
    _b().delete(source_id)


def source_ids() -> list[str]:
    return _b().source_ids()


# --- the original upload ---------------------------------------------------------

def read_csv(source_id: str, max_bytes: int | None = None) -> str | None:
    return _b().read_csv(source_id, max_bytes)


def write_csv(source_id: str, raw: bytes) -> None:
    _b().write_csv(source_id, raw)


# --- change log ----------------------------------------------------------------

def read_log(source_id: str) -> list[dict]:
    return _b().read_log(source_id)


def append_log(source_id: str, new: list[dict]) -> None:
    """Appends entries; keeps only the last MAX_BATCHES batches."""
    _b().append_log(source_id, new)


# --- part notes ------------------------------------------------------------------

def read_notes() -> dict[str, str]:
    return _b().read_notes()


def write_notes(notes: dict[str, str], log_entry: dict) -> None:
    _b().write_notes(notes, log_entry)


# --- reservations ------------------------------------------------------------------

def read_reservations() -> list[dict]:
    return _b().read_reservations()


def write_reservations(reservations: list[dict]) -> None:
    _b().write_reservations(reservations)


def reservations_signature() -> str:
    return _b().reservations_signature()


# --- remembered column mappings of generic CSV imports ------------------------------

def read_import_mappings() -> dict[str, dict[str, str]]:
    return _b().read_import_mappings()


def write_import_mapping(signature: str, mapping: dict[str, str]) -> None:
    _b().write_import_mapping(signature, mapping)


# --- CLI bridge: how the sandboxed toolkit finds the store ---------------------------

def cli_store_dir() -> Path:
    """The folder of `<id>.json` files the CLI reads: the files themselves, or the derived export."""
    return settings.COLLECTION_EXPORT_DIR if use_database() else settings.INVENTORIES_DIR


def cli_store_args() -> list[str]:
    """The `--store` option of the `inventory ...` CLI commands."""
    return ["--store", str(cli_store_dir())]


def cli_environment() -> dict[str, str]:
    """Environment variables the sandboxed toolkit reads to find the store."""
    return {"LDRAW_NOVA_INVENTORY_DIR": str(cli_store_dir())}


def scratch_dir(prefix: str) -> Path:
    """A fresh private working folder next to the store (dot-prefixed, so never listed as a source); the caller removes it."""
    folder = cli_store_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=folder))


# --- exact-row readers for the features that count what is owned -------------------

def exact_rows(source_id: str) -> tuple[str, list[tuple[str, int, int]]] | None:
    """(name, [(part, colour, qty)]) of one source's exact rows, or None when it is missing, unreadable or too big."""
    return _b().exact_rows(source_id)


def owned_totals() -> tuple[dict[tuple[str, int], int], str]:
    """Every source added together, exact rows only: ({(part, colour): qty}, signature that changes when any source does)."""
    return _b().owned_totals()
