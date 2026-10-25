"""The collection importer: the parts collection (inventories/ files) into PostgreSQL. Sibling of migrate_json.py.

    python -m migrate_collection                  dry run (the default): everything is inserted in a transaction that is
                                                  rolled back; prints counts and anomalies
    python -m migrate_collection --commit         import for real (one transaction per source), then verify, then write the
                                                  derived JSON copy for the toolkit CLI
    python -m migrate_collection --verify         compare the files with the database; exit code 1 on any mismatch
    python -m migrate_collection --export DIR     the inverse: write the file layout from the database into DIR (never
                                                  overwriting a file that exists, unless --force)
    --inventories-dir DIR                         the folder to read (default: LDRAW_NOVA_INVENTORIES_DIR or <data>/inventories)
    --json                                        print the report as JSON
    (the same through `python -m migrate_json --collection ...`)

* Imports what collection_store's file backend reads: `<id>.json` sources (rows, review "maybe" pieces, metadata), the
  original upload `<id>.csv`, the change log `<id>.log.jsonl`, `.collection-notes.json` + its log, the reservations and the
  remembered CSV column mappings.
* Idempotent: a source that is already in the database is left as it is (a file that differs from it is reported, never
  copied over), notes, reservations and mappings are added only when missing. A second run adds nothing.
* The files are only read: they stay as a frozen backup next to the derived copy in COLLECTION_EXPORT_DIR.
* `--verify` (also run after `--commit`): per source the row count, the piece total, the review items and pieces, the upload
  and the change-log length; then the notes, the log of the notes and the reservations. A source edited in the app after
  the import (its log is longer than the file's) is counted as `edited_since_import`, not as a mismatch.
* A successful commit + verify sets app_setting['collection_import'] = {done, at, counts}: the app then uses the database for
  the collection (collection_store.py). Every run is recorded in import_run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from migrate_json import Report, read_json, read_jsonl, record_run

NOTES_FILE, NOTES_LOG = ".collection-notes.json", ".collection-notes.log.jsonl"
RESERVATIONS_FILE, MAPPINGS_FILE = ".collection-reservations.json", ".collection-import-mappings.json"
KINDS = ("import", "set", "loose")


# --------------------------------------------------------------------------------------------- reading the files

class Files:
    """Everything the collection files hold, parsed once."""

    def __init__(self, folder: Path, report: Report):
        import inventories
        self.folder, self.report = folder, report
        self.sources: dict[str, dict] = {}
        self.csv: dict[str, str] = {}
        self.logs: dict[str, list[dict]] = {}
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            if not inventories.valid_id(path.stem):
                continue
            data = read_json(path)
            if not isinstance(data, dict) or not isinstance(data.get("rows", []), list):
                report.anomaly(f"{path.name}: not a readable source, skipped")
                continue
            data.setdefault("id", path.stem)
            if data["id"] != path.stem:
                report.anomaly(f"{path.name}: its id is {data['id']!r}; the file name wins")
                data["id"] = path.stem
            self.sources[path.stem] = data
            csv_path = folder / f"{path.stem}.csv"
            if csv_path.is_file():
                try:
                    self.csv[path.stem] = csv_path.read_text(encoding="utf-8")
                except (OSError, ValueError):
                    report.anomaly(f"{csv_path.name}: unreadable upload, skipped")
            entries, bad = read_jsonl(folder / f"{path.stem}.log.jsonl")
            if bad:
                report.anomaly(f"{path.stem}.log.jsonl: {bad} line(s) are not JSON objects, skipped")
            self.logs[path.stem] = entries
        notes = read_json(folder / NOTES_FILE)
        found = notes.get("notes") if isinstance(notes, dict) else None
        self.notes = {k: v for k, v in found.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(found, dict) else {}
        self.notes_log, bad = read_jsonl(folder / NOTES_LOG)
        res = read_json(folder / RESERVATIONS_FILE)
        found = res.get("reservations") if isinstance(res, dict) else None
        self.reservations = [r for r in found if isinstance(r, dict) and isinstance(r.get("rows"), list)] if isinstance(found, list) else []
        maps = read_json(folder / MAPPINGS_FILE)
        found = maps.get("mappings") if isinstance(maps, dict) else None
        self.mappings = {k: v for k, v in found.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(found, dict) else {}


def figures(data: dict) -> dict:
    """What verify compares: row count, piece total, review items and pieces (rows merged by part and colour, like the table)."""
    import collection_pg
    rows, _ = collection_pg.merged_rows(data.get("rows") or [])
    review = collection_pg.review_records(data.get("review") or [])
    return {"rows": len(rows), "pieces": sum(r["qty"] for r in rows), "review_items": len(review), "review_pieces": sum(r["qty"] for r in review)}


# --------------------------------------------------------------------------------------------- the import

class Importer:
    def __init__(self, files: Files, dry_run: bool, report: Report):
        import db
        self.db, self.files, self.dry, self.report = db, files, dry_run, report
        self._conn = self._outer = self._session = None

    @contextmanager
    def tx(self) -> Iterator[Any]:
        """One transaction per source: real in commit mode, a savepoint of a transaction that is never committed in a dry run."""
        if not self.dry:
            with self.db.session_scope() as session:
                yield session
            return
        from sqlalchemy.orm import Session
        if self._session is None:
            self._conn = self.db.get_engine().connect()
            self._outer = self._conn.begin()
            self._session = Session(bind=self._conn)
        with self._session.begin_nested():
            yield self._session

    def finish(self) -> None:
        if self._session is not None:
            self._session.close()
            self._outer.rollback()
            self._conn.close()

    def run(self) -> None:
        for sid, data in self.files.sources.items():
            self.source(sid, data)
        self.rest()
        self.report.count("sources_in_files", len(self.files.sources))

    def source(self, sid: str, data: dict) -> None:
        import collection_pg
        from sqlalchemy import select, update
        from sqlalchemy.exc import IntegrityError
        from db.models import PartLog, PartSource
        from pg_store import jsonb
        report = self.report
        figs = figures(data)
        report.sources[sid] = figs
        _, merged = collection_pg.merged_rows(data.get("rows") or [])
        if merged:
            report.anomaly(f"{sid}: {merged} repeated part+colour row(s) were added up")
        if data.get("kind") not in (None, *KINDS):
            report.anomaly(f"{sid}: unknown kind {data.get('kind')!r} stored as import")
        try:
            with self.tx() as session:
                present = session.scalar(select(PartSource.id).where(PartSource.id == sid)) is not None
                if present:
                    report.count("sources_already_imported")
                    old = collection_pg.load_from(session, sid)
                    if figures(old) != figs and len(self.files.logs.get(sid, [])) == _log_len(session, sid):
                        report.anomaly(f"changed: {sid} differs from its database copy; the database copy is kept as it is")
                    return
                collection_pg.write_source(session, data)
                if sid in self.files.csv:
                    session.execute(update(PartSource).where(PartSource.id == sid).values(csv_text=self.files.csv[sid].replace("\x00", "�")))
                entries = [e for e in self.files.logs.get(sid, [])]
                for i in range(0, len(entries), 500):
                    session.execute(PartLog.__table__.insert(), [dict(source_id=sid, batch=collection_pg._int(e.get("batch")), entry=jsonb(e))
                                                                  for e in entries[i:i + 500]])
                report.count("sources_imported")
                report.count("rows_imported", figs["rows"])
                report.count("pieces_imported", figs["pieces"])
                report.count("review_items_imported", figs["review_items"])
                report.count("log_entries_imported", len(entries))
        except IntegrityError:
            report.anomaly(f"{sid}: another source already has the name {data.get('name')!r}; not imported")
            report.sources[sid]["skipped"] = True

    def rest(self) -> None:
        from sqlalchemy import func, select
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from db.models import AppSetting, PartNote, PartNoteLog, PartReservation
        from pg_store import jsonb
        files, report = self.files, self.report
        with self.tx() as session:
            have = {p for p in session.scalars(select(PartNote.part))}
            for part, note in files.notes.items():
                if part not in have:
                    session.add(PartNote(part=part, note=note))
                    report.count("notes_imported")
            if files.notes_log and not session.scalar(select(func.count()).select_from(PartNoteLog)):
                for entry in files.notes_log:
                    session.add(PartNoteLog(entry=jsonb(entry)))
                report.count("note_log_entries_imported", len(files.notes_log))
            known = {i for i in session.scalars(select(PartReservation.id))}
            for r in files.reservations:
                rid = r.get("id")
                if not isinstance(rid, str) or not rid:
                    report.anomaly("a reservation without an id is skipped")
                    continue
                if rid not in known:
                    session.add(PartReservation(id=rid, body=jsonb(r)))
                    report.count("reservations_imported")
            if files.mappings:
                current = session.scalar(select(AppSetting.value).where(AppSetting.key == "collection_import_mappings")) or {}
                merged = {**files.mappings, **current}
                if merged != current:
                    statement = pg_insert(AppSetting).values(key="collection_import_mappings", value=jsonb(merged))
                    session.execute(statement.on_conflict_do_update(index_elements=["key"], set_={"value": statement.excluded.value}))
                    report.count("mappings_imported", len(merged) - len(current))
        report.count("notes_in_files", len(files.notes))
        report.count("reservations_in_files", len(files.reservations))


def _log_len(session, sid: str) -> int:
    from sqlalchemy import func, select
    from db.models import PartLog
    return session.scalar(select(func.count()).select_from(PartLog).where(PartLog.source_id == sid)) or 0


# --------------------------------------------------------------------------------------------- verify

def verify(files: Files, report: Report) -> None:
    import collection_pg
    import db
    from sqlalchemy import func, select
    from db.models import PartNote, PartNoteLog, PartReservation, PartSource
    matched = {"sources": 0, "rows": 0, "pieces": 0, "review_items": 0, "log_entries": 0, "notes": 0, "reservations": 0}
    with db.session_scope() as session:
        for sid, data in files.sources.items():
            src = session.get(PartSource, sid)
            if src is None:
                report.mismatch(f"source {sid} is not in the database")
                continue
            want = figures(data)
            got = figures(collection_pg.load_from(session, sid))
            logged = _log_len(session, sid)
            file_log = len(files.logs.get(sid, []))
            report.sources[sid] = {**want, "log": file_log}
            if logged != file_log:
                if logged > file_log:
                    report.count("edited_since_import")
                    matched["sources"] += 1
                    continue
                report.mismatch(f"{sid}: {file_log} change-log entries in the file, {logged} in the database")
                continue
            if got != want:
                report.mismatch(f"{sid}: files {want}, database {got}")
                continue
            if sid in files.csv and (src.csv_text or "") != files.csv[sid].replace("\x00", "�"):
                report.mismatch(f"{sid}: the stored upload differs from {sid}.csv")
                continue
            if data.get("name") and src.name != data["name"]:
                report.mismatch(f"{sid}: name {data['name']!r} in the file, {src.name!r} in the database")
                continue
            matched["sources"] += 1
            matched["rows"] += want["rows"]
            matched["pieces"] += want["pieces"]
            matched["review_items"] += want["review_items"]
            matched["log_entries"] += file_log
        notes = {p: n for p, n in session.execute(select(PartNote.part, PartNote.note))}
        missing = {p: n for p, n in files.notes.items() if notes.get(p) != n}
        if missing:
            report.mismatch(f"notes: {len(missing)} of {len(files.notes)} differ or are missing in the database")
        else:
            matched["notes"] = len(files.notes)
        if (session.scalar(select(func.count()).select_from(PartNoteLog)) or 0) < len(files.notes_log):
            report.mismatch(f"notes log: {len(files.notes_log)} entries in the file, fewer in the database")
        ids = {i for i in session.scalars(select(PartReservation.id))}
        absent = [r.get("id") for r in files.reservations if r.get("id") not in ids]
        if absent:
            report.mismatch(f"reservations missing in the database: {absent}")
        else:
            matched["reservations"] = len(files.reservations)
    report.extra["matched"] = matched
    report.extra["sources_in_files"] = len(files.sources)


# --------------------------------------------------------------------------------------------- export (the inverse)

def export(target: Path, force: bool, report: Report) -> None:
    import collection_pg
    import db
    from sqlalchemy import select
    from db.models import PartNoteLog
    written = 0

    def put(path: Path, text: str) -> None:
        nonlocal written
        if path.exists() and not force:
            report.anomaly(f"{path.name} exists, left as it is (use --force)")
            return
        path.write_text(text, encoding="utf-8")
        written += 1

    target.mkdir(parents=True, exist_ok=True)
    for sid in collection_pg.source_ids():
        put(target / f"{sid}.json", collection_pg._dump(collection_pg.load(sid)).decode())
        csv_text = collection_pg.read_csv(sid)
        if csv_text is not None:
            put(target / f"{sid}.csv", csv_text)
        entries = collection_pg.read_log(sid)
        if entries:
            put(target / f"{sid}.log.jsonl", "".join(json.dumps(e) + "\n" for e in entries))
    notes = collection_pg.read_notes()
    if notes:
        put(target / NOTES_FILE, collection_pg._dump({"schema": 1, "notes": dict(sorted(notes.items()))}).decode())
    with db.session_scope() as session:
        log = list(session.scalars(select(PartNoteLog.entry).order_by(PartNoteLog.id)))
    if log:
        put(target / NOTES_LOG, "".join(json.dumps(e) + "\n" for e in log))
    reservations = collection_pg.read_reservations()
    if reservations:
        put(target / RESERVATIONS_FILE, collection_pg._dump({"schema": 1, "reservations": reservations}).decode())
    mappings = collection_pg.read_import_mappings()
    if mappings:
        put(target / MAPPINGS_FILE, collection_pg._dump({"schema": 1, "mappings": mappings}).decode())
    report.count("files_written", written)


# --------------------------------------------------------------------------------------------- runs

def run_import(folder: Path, dry_run: bool) -> Report:
    report = Report("dry-run" if dry_run else "commit")
    started = dt.datetime.now(dt.timezone.utc)
    importer = Importer(Files(folder, report), dry_run, report)
    try:
        importer.run()
    finally:
        importer.finish()
    record_run(report, started)
    return report


def run_verify(folder: Path) -> Report:
    report = Report("verify")
    started = dt.datetime.now(dt.timezone.utc)
    verify(Files(folder, report), report)
    record_run(report, started)
    return report


def mark_done(report: Report) -> None:
    import db
    from sqlalchemy import func
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from db.models import AppSetting
    value = {"done": True, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             "counts": {k: v for k, v in report.counts.items() if k.endswith("_in_files")}}
    statement = pg_insert(AppSetting).values(key="collection_import", value=value)
    with db.session_scope() as session:
        session.execute(statement.on_conflict_do_update(index_elements=["key"], set_={"value": statement.excluded.value, "updated_at": func.now()}))


def import_and_verify(folder: Path, dry_first: bool = True) -> tuple[bool, list[Report]]:
    """dry run, commit, verify; marks the collection import done when all of it passed and writes the derived JSON copy."""
    reports = []
    if dry_first:
        reports.append(run_import(folder, dry_run=True))
    committed = run_import(folder, dry_run=False)
    reports.append(committed)
    verified = run_verify(folder)
    reports.append(verified)
    if verified.ok:
        mark_done(committed)
        import collection_pg
        committed.count("export_sources_written", collection_pg.sync_export())
    return verified.ok, reports


def summary_text(report: Report) -> str:
    lines = [f"== collection {report.mode}: {'OK' if report.ok else 'MISMATCHES'} =="]
    lines += [f"  {name}: {value}" for name, value in sorted(report.counts.items())]
    if report.extra.get("matched"):
        lines.append("  verified (files == database): " + ", ".join(f"{k}={v}" for k, v in report.extra["matched"].items()))
    for sid, figs in sorted(report.sources.items()):
        lines.append(f"  source {sid}: " + ", ".join(f"{k}={v}" for k, v in figs.items()))
    lines += [f"  anomaly: {t}" for t in report.anomalies]
    lines += [f"  MISMATCH: {t}" for t in report.mismatches]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="migrate_collection", description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="insert in a rolled-back transaction (default)")
    mode.add_argument("--commit", action="store_true", help="import, verify, then write the derived JSON copy")
    mode.add_argument("--verify", action="store_true", help="compare the files with the database")
    mode.add_argument("--export", metavar="DIR", help="write the file layout from the database into DIR")
    parser.add_argument("--force", action="store_true", help="with --export: overwrite files that exist")
    parser.add_argument("--inventories-dir", help="the folder to read (default LDRAW_NOVA_INVENTORIES_DIR or <data>/inventories)")
    parser.add_argument("--data-dir", help="the data folder (default LDRAW_NOVA_DATA_DIR or /data)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    if args.data_dir:
        os.environ["LDRAW_NOVA_DATA_DIR"] = args.data_dir
    import db
    import settings
    from db import migrate
    if db.get_engine() is None:
        print("no database is configured (password file or LDRAW_NOVA_DATABASE_URL)", file=sys.stderr)
        return 2
    if not migrate.upgrade_when_ready(timeout_s=10):
        print("the database is not reachable or could not be migrated", file=sys.stderr)
        return 2
    folder = Path(args.inventories_dir) if args.inventories_dir else settings.INVENTORIES_DIR
    ok = True
    if args.export:
        report = Report("export")
        export(Path(args.export), args.force, report)
        reports = [report]
    elif args.verify:
        reports = [run_verify(folder)]
        ok = reports[0].ok
    elif args.commit:
        ok, reports = import_and_verify(folder)
    else:
        reports = [run_import(folder, dry_run=True)]
    for report in reports:
        print(json.dumps(report.as_dict(), indent=2, default=str) if args.json else summary_text(report))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
