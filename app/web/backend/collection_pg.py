"""The PostgreSQL backend of the parts collection store (same functions as collection_files.py; collection_store.py picks one).

A source dict (what `<id>.json` held) is spread over `part_source` (metadata columns, `extra` for any other top-level key,
`csv_text` for the original upload), `part_row` (exact rows), `part_review` (the "maybe" pieces) and rebuilt by `load`, so the
code above sees the same dicts. `part_log` is the change log (one row per log line), `part_note`/`part_note_log` the notes,
`part_reservation` the parts held by built models, app_setting['collection_import_mappings'] the remembered CSV mappings.

After every commit the derived JSON copy in settings.COLLECTION_EXPORT_DIR is updated (`<id>.json` per source in the exact
file format, plus `.collection-reservations.json` and `.collection-notes.json`): the sandboxed toolkit CLI and `buildable`
read that folder through `--store` and never reach the database. `sync_export()` regenerates all of it (at start).
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from sqlalchemy import delete as sql_delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

import db
import fsutil
import inventories
import settings
from db.models import AppSetting, PartLog, PartNote, PartNoteLog, PartReservation, PartReview, PartRow, PartSource
from inventories import InventoryError
from pg_store import jsonb, to_dt, to_float

log = logging.getLogger("collection_pg")

lock = fsutil.lock
MAX_BATCHES = 500
MAX_FILE_BYTES = 64 * 1024 * 1024
KINDS = ("import", "set", "loose")
SOURCE_KEYS = {"id", "name", "created_at", "source_filename", "kind", "schema", "set", "origin", "import", "totals", "warnings",
               "unmapped_parts", "unmapped_colours", "rows", "review"}
ROW_COLUMNS = ("part", "colour", "qty", "rb_part", "rb_colour")
REVIEW_COLUMNS = ("id", "part", "family", "candidates", "qty", "brickit_part", "note")
MAPPINGS_KEY = "collection_import_mappings"
CHUNK = 2000

_cache: OrderedDict[str, tuple[Any, dict]] = OrderedDict()
_cache_lock = threading.Lock()
_pending_csv: dict[str, str] = {}             # an upload written before its source row exists (commit order of collection.py)


def _dump(data: Any) -> bytes:
    return (json.dumps(data, indent=2) + "\n").encode()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _opt_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


# --- dict <-> rows ----------------------------------------------------------------

def merged_rows(rows: list) -> tuple[list[dict], int]:
    """The rows as part_row stores them (one per part and colour: a repeated pair is added up) and how many were merged."""
    out: dict[tuple[str, int], dict] = {}
    merged = 0
    for pos, r in enumerate(rows):
        extra = {k: v for k, v in r.items() if k not in ROW_COLUMNS}
        key = (str(r["part"]), _int(r["colour"]))
        if key in out:
            out[key]["qty"] += max(0, _int(r.get("qty")))
            merged += 1
            continue
        out[key] = dict(part=key[0], colour=key[1], qty=max(0, _int(r.get("qty"))), rb_part=_opt_str(r.get("rb_part")),
                        rb_colour=None if r.get("rb_colour") is None else _int(r["rb_colour"]), pos=pos,
                        extra=jsonb(extra) if extra else None)
    return list(out.values()), merged


def review_records(review: list) -> list[dict]:
    out: dict[str, dict] = {}
    for pos, r in enumerate(review):
        extra = {k: v for k, v in r.items() if k not in REVIEW_COLUMNS}
        rid = str(r.get("id") if r.get("id") is not None else f"r{pos}")
        out[rid] = dict(id=rid, part=str(r.get("part", "")), family=_opt_str(r.get("family")),
                        candidates=[_int(c) for c in (r.get("candidates") or [])], qty=max(1, _int(r.get("qty"), 1)),
                        brickit_part=_opt_str(r.get("brickit_part")), note=_opt_str(r.get("note")), pos=pos,
                        extra=jsonb(extra) if extra else None)
    return list(out.values())


def _hash(rows: list[dict], review: list[dict]) -> str:
    material = json.dumps([[(r["part"], r["colour"], r["qty"], r["rb_part"], r["rb_colour"], r["pos"], r["extra"]) for r in rows],
                           [(r["id"], r["part"], r["family"], r["candidates"], r["qty"], r["brickit_part"], r["note"], r["pos"], r["extra"])
                            for r in review]], sort_keys=True, default=str)
    return hashlib.sha256(material.encode()).hexdigest()


def _row_dict(r: PartRow) -> dict:
    out: dict = {"part": r.part, "colour": r.colour, "qty": r.qty}
    if r.rb_part is not None:
        out["rb_part"] = r.rb_part
    if r.rb_colour is not None:
        out["rb_colour"] = r.rb_colour
    out.update(r.extra or {})
    return out


def _review_dict(r: PartReview) -> dict:
    out: dict = {"id": r.id, "part": r.part}
    if r.family is not None:
        out["family"] = r.family
    out["candidates"] = list(r.candidates or [])
    out["qty"] = r.qty
    if r.brickit_part is not None:
        out["brickit_part"] = r.brickit_part
    if r.note is not None:
        out["note"] = r.note
    out.update(r.extra or {})
    return out


def _source_dict(src: PartSource, rows: list[PartRow], review: list[PartReview]) -> dict:
    data: dict = {"id": src.id, "name": src.name, "created_at": to_float(src.created_at), "source_filename": src.source_filename,
                  "kind": src.kind, "schema": src.schema_version}
    for key, value in (("set", src.set_info), ("origin", src.origin), ("import", src.import_meta), ("totals", src.totals)):
        if value is not None:
            data[key] = value
    data["rows"] = [_row_dict(r) for r in rows]
    if review:
        data["review"] = [_review_dict(r) for r in review]
    data["unmapped_parts"], data["unmapped_colours"], data["warnings"] = src.unmapped_parts, src.unmapped_colours, src.warnings
    data.update(src.extra or {})
    return data


def source_values(data: dict) -> dict:
    """The part_source columns of a source dict."""
    kind = data.get("kind") if data.get("kind") in KINDS else "import"
    return dict(name=str(data.get("name") or data["id"]), kind=kind, schema_version=_int(data.get("schema"), 1),
                source_filename=_opt_str(data.get("source_filename")), origin=jsonb(data.get("origin")),
                set_info=jsonb(data.get("set")), import_meta=jsonb(data.get("import")), totals=jsonb(data.get("totals")),
                warnings=jsonb(data.get("warnings") or []), unmapped_parts=jsonb(data.get("unmapped_parts") or []),
                unmapped_colours=jsonb(data.get("unmapped_colours") or []),
                extra=jsonb({k: v for k, v in data.items() if k not in SOURCE_KEYS}),
                created_at=to_dt(data.get("created_at") if data.get("created_at") is not None else time.time()))


def write_source(session, data: dict, touch: bool = True) -> None:
    """Upserts one source and, when they changed, its rows and review items (inside the caller's transaction)."""
    sid = data["id"]
    rows, _ = merged_rows(data.get("rows") or [])
    review = review_records(data.get("review") or [])
    digest = _hash(rows, review)
    values = source_values(data)
    old_hash = session.scalar(select(PartSource.rows_hash).where(PartSource.id == sid))
    exists = session.scalar(select(PartSource.id).where(PartSource.id == sid)) is not None
    csv_text = _pending_csv.pop(sid, None)
    if csv_text is not None:
        values["csv_text"] = csv_text
    statement = pg_insert(PartSource).values(id=sid, rows_hash=digest, **values)
    changes = {k: statement.excluded[k] for k in (*values, "rows_hash")}
    if touch:
        changes["updated_at"] = func.clock_timestamp()
    session.execute(statement.on_conflict_do_update(index_elements=["id"], set_=changes))
    if not exists or old_hash != digest:
        session.execute(sql_delete(PartRow).where(PartRow.source_id == sid))
        session.execute(sql_delete(PartReview).where(PartReview.source_id == sid))
        for i in range(0, len(rows), CHUNK):
            session.execute(pg_insert(PartRow), [dict(source_id=sid, **r) for r in rows[i:i + CHUNK]])
        if review:
            session.execute(pg_insert(PartReview), [dict(source_id=sid, **r) for r in review])


# --- sources ---------------------------------------------------------------------

def exists(source_id: str) -> bool:
    if not inventories.valid_id(source_id):
        return False
    with db.session_scope() as session:
        return session.scalar(select(PartSource.id).where(PartSource.id == source_id)) is not None


def load(source_id: str) -> dict:
    """The stored source, shared and cached: never modify it (InventoryError 404 when missing)."""
    if not inventories.valid_id(source_id):
        raise InventoryError(404, "no such inventory")
    with db.session_scope() as session:
        src = session.get(PartSource, source_id)
        if src is None:
            raise InventoryError(404, "no such inventory")
        with _cache_lock:
            cached = _cache.get(source_id)
            if cached is not None and cached[0] == src.updated_at:
                _cache.move_to_end(source_id)
                return cached[1]
        rows = session.scalars(select(PartRow).where(PartRow.source_id == source_id).order_by(PartRow.pos, PartRow.part, PartRow.colour)).all()
        review = session.scalars(select(PartReview).where(PartReview.source_id == source_id).order_by(PartReview.pos, PartReview.id)).all()
        data = _source_dict(src, rows, review)
    with _cache_lock:
        _cache[source_id] = (src.updated_at, data)
        while len(_cache) > 8:
            _cache.popitem(last=False)
    return data


def load_from(session, source_id: str) -> dict:
    """The source as a fresh dict, read inside the caller's session (no cache)."""
    src = session.get(PartSource, source_id)
    if src is None:
        raise InventoryError(404, "no such inventory")
    rows = session.scalars(select(PartRow).where(PartRow.source_id == source_id).order_by(PartRow.pos, PartRow.part, PartRow.colour)).all()
    review = session.scalars(select(PartReview).where(PartReview.source_id == source_id).order_by(PartReview.pos, PartReview.id)).all()
    return _source_dict(src, rows, review)


def load_copy(source_id: str) -> dict:
    import copy
    return copy.deepcopy(load(source_id))


def save(data: dict) -> None:
    try:
        with db.session_scope() as session:
            write_source(session, data)
    except IntegrityError as exc:
        raise InventoryError(409, f"An inventory called {data.get('name')!r} already exists") from exc
    _export_source(data["id"])


def delete(source_id: str) -> None:
    if not inventories.valid_id(source_id):
        raise InventoryError(404, "no such inventory")
    with db.session_scope() as session:
        if session.execute(sql_delete(PartSource).where(PartSource.id == source_id)).rowcount == 0:
            raise InventoryError(404, "no such inventory")
    with _cache_lock:
        _cache.pop(source_id, None)
    _pending_csv.pop(source_id, None)
    _export_source(source_id)


def source_ids() -> list[str]:
    with db.session_scope() as session:
        return [i for i in session.scalars(select(PartSource.id).order_by(PartSource.id)) if inventories.valid_id(i)]


# --- the original upload ---------------------------------------------------------

def read_csv(source_id: str, max_bytes: int | None = None) -> str | None:
    if not inventories.valid_id(source_id):
        return None
    with db.session_scope() as session:
        found = session.execute(select(PartSource.csv_text, func.octet_length(PartSource.csv_text)).where(PartSource.id == source_id)).first()
    if found is None or found[0] is None or (max_bytes is not None and found[1] > max_bytes):
        return None
    return found[0]


def write_csv(source_id: str, raw: bytes) -> None:
    text = raw.decode("utf-8", errors="replace").replace("\x00", "�")
    with db.session_scope() as session:
        if session.execute(update(PartSource).where(PartSource.id == source_id).values(csv_text=text)).rowcount == 0:
            _pending_csv[source_id] = text          # the source row is saved right after (collection._store_new)


# --- change log ------------------------------------------------------------------

def read_log(source_id: str) -> list[dict]:
    with db.session_scope() as session:
        return list(session.scalars(select(PartLog.entry).where(PartLog.source_id == source_id).order_by(PartLog.id)))


def append_log(source_id: str, new: list[dict]) -> None:
    """Appends entries; keeps only the last MAX_BATCHES batches."""
    if not new:
        return
    with db.session_scope() as session:
        session.execute(pg_insert(PartLog), [dict(source_id=source_id, batch=_int(e.get("batch")), entry=jsonb(e)) for e in new])
        cutoff = session.scalar(select(PartLog.batch).where(PartLog.source_id == source_id).group_by(PartLog.batch)
                                .order_by(PartLog.batch.desc()).offset(MAX_BATCHES).limit(1))
        if cutoff is not None:
            session.execute(sql_delete(PartLog).where(PartLog.source_id == source_id, PartLog.batch <= cutoff))


# --- part notes ------------------------------------------------------------------

def read_notes() -> dict[str, str]:
    with db.session_scope() as session:
        return {part: note for part, note in session.execute(select(PartNote.part, PartNote.note))}


def write_notes(notes: dict[str, str], log_entry: dict) -> None:
    with db.session_scope() as session:
        current = {part: note for part, note in session.execute(select(PartNote.part, PartNote.note))}
        for part in set(current) - set(notes):
            session.execute(sql_delete(PartNote).where(PartNote.part == part))
        for part, note in notes.items():
            if current.get(part) != note:
                statement = pg_insert(PartNote).values(part=part, note=note)
                session.execute(statement.on_conflict_do_update(index_elements=["part"], set_={"note": statement.excluded.note, "updated_at": func.now()}))
        session.add(PartNoteLog(entry=jsonb(log_entry)))
    _export_notes()


# --- reservations ----------------------------------------------------------------

def read_reservations() -> list[dict]:
    with db.session_scope() as session:
        found = list(session.scalars(select(PartReservation.body).order_by(PartReservation.seq)))
    return [r for r in found if isinstance(r, dict) and isinstance(r.get("rows"), list)]


def write_reservations(reservations: list[dict]) -> None:
    records = []
    for r in reservations:
        r = dict(r)
        r["id"] = str(r.get("id") or "res-" + secrets.token_hex(4))
        records.append(r)
    with db.session_scope() as session:
        keep = [r["id"] for r in records]
        session.execute(sql_delete(PartReservation).where(PartReservation.id.not_in(keep)) if keep else sql_delete(PartReservation))
        for r in records:
            statement = pg_insert(PartReservation).values(id=r["id"], body=jsonb(r))
            session.execute(statement.on_conflict_do_update(index_elements=["id"], set_={"body": statement.excluded.body}))
    _export_reservations()


def reservations_signature() -> str:
    with db.session_scope() as session:
        count, newest, ids = session.execute(select(func.count(), func.coalesce(func.max(PartReservation.seq), 0),
                                                    func.coalesce(func.string_agg(PartReservation.id, ","), ""))).one()
    return f"{count}:{newest}:{hashlib.md5(ids.encode()).hexdigest()[:8]}" if count else ""


# --- remembered column mappings of generic CSV imports ------------------------------

def read_import_mappings() -> dict[str, dict[str, str]]:
    with db.session_scope() as session:
        found = session.scalar(select(AppSetting.value).where(AppSetting.key == MAPPINGS_KEY))
    if not isinstance(found, dict):
        return {}
    return {k: v for k, v in found.items() if isinstance(k, str) and isinstance(v, dict) and all(isinstance(x, str) for x in v.values())}


def write_import_mapping(signature: str, mapping: dict[str, str]) -> None:
    mappings = read_import_mappings()
    mappings[signature] = mapping
    for old in list(mappings)[:-50]:                    # a spreadsheet habit is a handful of layouts, not hundreds
        del mappings[old]
    statement = pg_insert(AppSetting).values(key=MAPPINGS_KEY, value=jsonb(mappings))
    with db.session_scope() as session:
        session.execute(statement.on_conflict_do_update(index_elements=["key"], set_={"value": statement.excluded.value, "updated_at": func.now()}))


# --- exact-row readers -------------------------------------------------------------

def exact_rows(source_id: str) -> tuple[str, list[tuple[str, int, int]]] | None:
    if not inventories.valid_id(source_id):
        return None
    try:
        with db.session_scope() as session:
            name = session.scalar(select(PartSource.name).where(PartSource.id == source_id))
            if name is None:
                return None
            rows = session.execute(select(PartRow.part, PartRow.colour, PartRow.qty).where(PartRow.source_id == source_id, PartRow.qty > 0)
                                   .order_by(PartRow.pos, PartRow.part, PartRow.colour)).all()
        return str(name or source_id), [(str(p), int(c), int(q)) for p, c, q in rows]
    except Exception:  # noqa: BLE001
        log.warning("exact rows of %s unreadable", source_id)
        return None


def owned_totals() -> tuple[dict[tuple[str, int], int], str]:
    owned: dict[tuple[str, int], int] = {}
    with db.session_scope() as session:
        stamps = session.execute(select(PartSource.id, PartSource.updated_at).order_by(PartSource.id)).all()
        rows = session.execute(select(PartRow.part, PartRow.colour, func.sum(PartRow.qty)).group_by(PartRow.part, PartRow.colour)).all()
    for part, colour, qty in rows:
        key = (str(part).casefold().removesuffix(".dat"), colour)
        owned[key] = owned.get(key, 0) + int(qty)
    return owned, "|".join(f"{i}:{u.isoformat()}" for i, u in stamps if inventories.valid_id(i))


# --- the derived JSON copy for the toolkit CLI ---------------------------------------

def _write_if_changed(path, payload: bytes) -> None:
    try:
        if path.read_bytes() == payload:
            return
    except OSError:
        pass
    fsutil.atomic_write(path, payload)


def _guard(what: str, fn) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        log.error("collection export (%s) failed: %s", what, exc.__class__.__name__)


def _export_source(source_id: str) -> None:
    def work():
        path = settings.COLLECTION_EXPORT_DIR / f"{source_id}.json"
        try:
            data = load(source_id)
        except InventoryError:
            path.unlink(missing_ok=True)
            return
        _write_if_changed(path, _dump(data))
    _guard(source_id, work)


def _export_reservations() -> None:
    _guard("reservations", lambda: _write_if_changed(settings.COLLECTION_EXPORT_DIR / ".collection-reservations.json",
                                                     _dump({"schema": 1, "reservations": read_reservations()})))


def _export_notes() -> None:
    _guard("notes", lambda: _write_if_changed(settings.COLLECTION_EXPORT_DIR / ".collection-notes.json",
                                              _dump({"schema": 1, "notes": dict(sorted(read_notes().items()))})))


def sync_export() -> int:
    """Regenerates the whole derived copy from the database; removes `<id>.json` files of sources that no longer exist
    (not when the export folder is the original inventories folder). Returns the number of sources written."""
    folder = settings.COLLECTION_EXPORT_DIR
    ids = source_ids()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for sid in ids:
            _export_source(sid)
        _export_reservations()
        _export_notes()
        if folder.resolve() != settings.INVENTORIES_DIR.resolve():
            for path in folder.glob("*.json"):
                if inventories.valid_id(path.stem) and path.stem not in ids:
                    path.unlink(missing_ok=True)
    except OSError as exc:
        log.error("collection export failed: %s", exc)
    return len(ids)
