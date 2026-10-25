"""The JSON importer: chats, builds and everything that goes with them, from the files in /data into PostgreSQL.

    python -m migrate_json                  dry run (the default): everything is parsed and inserted in a transaction that is
                                            rolled back; prints counts and anomalies, writes nothing but an import_run row
    python -m migrate_json --commit         import for real (one transaction per build and per chat), then verify
    python -m migrate_json --verify         compare the files with the database; exit code 1 on any mismatch
    python -m migrate_json --export DIR     the inverse: write the JSON layout from the database into DIR (never overwriting
                                            a file that exists, unless --force)
    --data-dir DIR                          the data folder to read (default: LDRAW_NOVA_DATA_DIR or /data)
    --json                                  print the report as JSON

Properties (docs/plans/postgres/PROPOSAL.md section 5):

* Idempotent: every insert is ON CONFLICT DO NOTHING on the natural key (chat + message id, build + version, ...). A second
  run adds nothing. A JSON file that changed after it was imported is reported (`changed`), never copied over the rows.
* The JSON files are only read. They stay as a frozen backup.
* `--verify` (also run after `--commit`): per chat the message count and a canonical hash of the messages, the chat's fields,
  the model records (and their files' sha256), worker steps, the cost sums; per build the record, versions, draft and
  proposals. A mismatch makes the run `ok = false` and the migration is not marked done.
* A successful commit + verify sets app_setting['json_import'] = {done, at, counts}; the app then reads the database only
  (storage.py). Every run is recorded in import_run.

Not imported here: the parts collection (inventories/), which has its own storage module.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Optional

REPORT_DIR_NAME = "_migration"
BATCH = 400
ORIGINS = ("generator", "ai", "editor", "import")
SOURCE_KINDS = ("generator", "ai", "import", "editor", "empty")
CHAT_KEYS = {"id", "title", "llm_model_id", "created_at", "updated_at", "options", "build_id"}
DRAFT_COLUMNS = ("base_version", "base_file", "base_revision", "saved_revision", "base_undo_depth")


# --------------------------------------------------------------------------------------------- helpers

def read_jsonl(path: Path) -> tuple[list[dict], int]:
    """(records, number of lines that are not a JSON object: truncated by a crash, blank lines excluded)."""
    records, bad = [], 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(record, dict):
            records.append(record)
        else:
            bad += 1
    return records, bad


def read_json(path: Path) -> Optional[dict]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def canonical(messages: list[dict]) -> str:
    """sha256 over the messages in id order as canonical JSON (sorted keys, no spaces): what a chat's history IS."""
    from pg_store import clean_json
    ordered = sorted((clean_json(m) for m in messages), key=lambda m: m["id"])
    return hashlib.sha256(json.dumps(ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def fingerprint(*paths: Path) -> dict:
    found = {}
    for path in paths:
        try:
            stat = path.stat()
            found[path.name] = [stat.st_size, stat.st_mtime_ns]
        except OSError:
            continue
    return found


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class Report:
    def __init__(self, mode: str):
        self.mode = mode
        self.counts: dict[str, int] = {}
        self.anomalies: list[str] = []
        self.mismatches: list[str] = []
        self.sources: dict[str, dict] = {}
        self.extra: dict[str, Any] = {}

    def count(self, name: str, n: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + n

    def anomaly(self, text: str) -> None:
        self.anomalies.append(text)

    def mismatch(self, text: str) -> None:
        self.mismatches.append(text)

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def as_dict(self) -> dict:
        return {"mode": self.mode, "ok": self.ok, "counts": self.counts, "anomalies": self.anomalies,
                "mismatches": self.mismatches, "sources": self.sources, **self.extra}


# --------------------------------------------------------------------------------------------- reading the JSON state

class Source:
    """Everything the JSON files hold, parsed once: used by the importer, the verifier and the tests."""

    def __init__(self, data_dir: Path):
        import settings
        from store import ChatStore
        self.data_dir = data_dir
        self.chats_dir = data_dir / "chats"
        self.builds_dir = data_dir / "builds"
        # A file store on this data folder: the legacy derivation of builds (builds._legacy) is its code, not a copy.
        self.files = ChatStore(self.chats_dir, data_dir / "output")
        self.settings = settings

    def chat_ids(self) -> list[str]:
        if not self.chats_dir.is_dir():
            return []
        return sorted(p.name for p in self.chats_dir.iterdir()
                      if p.is_dir() and self.files.valid_id(p.name) and not self.files.is_build(p.name) and (p / "chat.json").is_file())

    def rel(self, base: Path, ref: str) -> str:
        """A model reference (relative to the chat or build folder) as a path under the data folder, or absolute."""
        target = Path(os.path.normpath(base / ref))
        try:
            return target.relative_to(self.data_dir).as_posix()
        except ValueError:
            return target.as_posix()


def model_rows(source: Source, base: Path, records: list[dict], owner: str, report: Report, build: bool) -> list[dict]:
    """The model_record values of a chat's or a build's models.jsonl: file normalised, sha256 and parts computed, a
    duplicate version keeps the latest record (and is reported)."""
    from pg_store import file_sha256, jsonb, part_count, to_dt
    import re
    by_version: dict[int, dict] = {}
    for position, record in enumerate(records, start=1):
        ref = record.get("model")
        if not isinstance(ref, str) or not isinstance(record.get("id"), str):
            report.anomaly(f"{owner}: a model record without id or model is skipped")
            continue
        version = record.get("version")
        if not is_int(version) or version < 1:
            match = re.search(r"-v(\d+)$", Path(ref).stem) if build else None
            version = int(match.group(1)) if match else position
        origin = record.get("origin")
        if origin is not None and origin not in ORIGINS:
            report.anomaly(f"{owner}: unknown model origin {origin!r} dropped")
            origin = None
        path = Path(os.path.normpath(base / ref))
        row = dict(id=record["id"], version=version, name=str(record.get("name") or Path(ref).stem), file=source.rel(base, ref),
                   sha256=file_sha256(path), parts=part_count(path) if path.is_file() else None,
                   warnings=jsonb(record.get("warnings") if isinstance(record.get("warnings"), list) else []),
                   source=record.get("source") if isinstance(record.get("source"), str) else None, origin=origin,
                   created_at=to_dt(record.get("created_at")))
        if version in by_version:
            report.anomaly(f"{owner}: version {version} is recorded twice; the later record is kept")
        by_version[version] = row
    return [by_version[v] for v in sorted(by_version, key=lambda v: (by_version[v]["created_at"], v))]


# --------------------------------------------------------------------------------------------- the import

class Importer:
    def __init__(self, source: Source, dry_run: bool, report: Report, previous: Optional[dict] = None):
        import db
        self.db, self.source, self.dry, self.report = db, source, dry_run, report
        self.previous = previous or {}
        self._conn = self._outer = self._session = None

    @contextmanager
    def tx(self) -> Iterator[Any]:
        """One transaction per entity: real in commit mode, a savepoint of a transaction that is never committed in a dry run."""
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

    def changed(self, key: str, files: dict) -> None:
        self.report.sources[key] = files
        old = self.previous.get(key)
        if old is not None and old != files:
            self.report.anomaly(f"changed: {key} changed after it was imported; the database copy is kept as it is")

    # --- builds

    def run(self) -> None:
        import builds
        from db.models import Build
        files = self.source.files
        index = builds.Index(files)
        slugs = builds.all_slugs(files)
        for slug in slugs:
            record = builds.get(files, slug, index)
            if record is None:
                continue
            self.build(slug, record)
        for chat_id in self.source.chat_ids():
            self.chat(chat_id, set(slugs))
        self.report.count("builds_in_files", len(slugs))
        self.report.count("chats_in_files", len(self.source.chat_ids()))

    def build(self, slug: str, record: dict) -> None:
        from sqlalchemy import func, select
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from db.models import Build, BuildDraft, ModelRecord, Proposal
        from pg_store import jsonb, to_dt
        folder = self.source.builds_dir / slug
        source = dict(record.get("source") or {"kind": "empty"})
        if source.get("kind") not in SOURCE_KINDS:
            self.report.anomaly(f"build {slug}: unknown source kind {source.get('kind')!r} stored as import")
            source["kind"] = "import"
        has_file = (folder / "build.json").is_file()
        if not has_file:
            self.report.count("builds_derived_from_files")
        models, _ = read_jsonl(folder / "models.jsonl")
        rows = model_rows(self.source, folder, models, f"build {slug}", self.report, build=True)
        draft = read_json(folder / "draft.json")
        proposals = read_proposals(slug, folder, self.report)
        listed = [c for c in record.get("chats", []) if isinstance(c, str)]
        self.changed(f"build:{slug}", fingerprint(folder / "build.json", folder / "models.jsonl", folder / "draft.json"))
        with self.tx() as session:
            before = [session.scalar(select(func.count()).select_from(t).where(c == slug))
                      for t, c in ((Build, Build.id), (ModelRecord, ModelRecord.build_id), (BuildDraft, BuildDraft.build_id), (Proposal, Proposal.build_id))]
            session.execute(pg_insert(Build).values(id=slug, title=str(record.get("title") or slug), source=jsonb(source),
                                                    created_at=to_dt(record.get("created_at")), updated_at=to_dt(record.get("updated_at")))
                            .on_conflict_do_nothing())
            if rows:
                session.execute(pg_insert(ModelRecord).values([{**r, "build_id": slug} for r in rows]).on_conflict_do_nothing())
            if draft is not None:
                values = {name: draft.get(name) for name in DRAFT_COLUMNS}
                for name in ("base_revision", "saved_revision", "base_undo_depth"):
                    values[name] = values[name] if is_int(values[name]) else 0
                values["base_version"] = values["base_version"] if is_int(values["base_version"]) else None
                session.execute(pg_insert(BuildDraft).values(
                    build_id=slug, **values, updated_at=to_dt(draft.get("updated_at")),
                    extra=jsonb({k: v for k, v in draft.items() if k not in DRAFT_COLUMNS and k != "updated_at"}))
                                .on_conflict_do_nothing())
            if proposals:
                session.execute(pg_insert(Proposal).values([{**p, "build_id": slug} for p in proposals]).on_conflict_do_nothing())
            after = [session.scalar(select(func.count()).select_from(t).where(c == slug))
                     for t, c in ((Build, Build.id), (ModelRecord, ModelRecord.build_id), (BuildDraft, BuildDraft.build_id), (Proposal, Proposal.build_id))]
        for name, b, a in zip(("builds", "build_versions", "build_drafts", "proposals"), before, after):
            self.report.count(f"{name}_added", a - b)
        self.report.count("build_versions_in_files", len(rows))
        self.report.count("build_drafts_in_files", 1 if draft is not None else 0)
        self.report.count("proposals_in_files", len(proposals))
        self.report.extra.setdefault("chat_lists_not_linked", [])
        for chat_id in listed:        # build.json listed a chat that does not say it belongs to the build: the link is the chat's
            chat = read_json(self.source.chats_dir / chat_id / "chat.json")
            if chat is not None and chat.get("build_id") != slug:
                self.report.extra["chat_lists_not_linked"].append(f"{slug}:{chat_id}")

    # --- chats

    def chat(self, chat_id: str, slugs: set[str]) -> None:
        from sqlalchemy import func, select, update
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from db.models import Chat, Message, ModelRecord, UsageEvent, WorkerStep
        from pg_store import jsonb, to_dt
        folder = self.source.chats_dir / chat_id
        record = read_json(folder / "chat.json") or {}
        if record.get("id") not in (None, chat_id):
            self.report.anomaly(f"chat {chat_id}: chat.json names id {record.get('id')!r}; the folder name is used")
        for key in sorted(set(record) - CHAT_KEYS):
            self.report.anomaly(f"chat {chat_id}: unknown key {key!r} in chat.json is not imported")
        build_id = record.get("build_id")
        if build_id and build_id not in slugs:
            self.report.anomaly(f"chat {chat_id}: build {build_id!r} does not exist; the chat is imported unattached")
            build_id = None
        raw, bad = read_jsonl(folder / "messages.jsonl")
        if bad:
            self.report.anomaly(f"chat {chat_id}: {bad} unreadable line(s) in messages.jsonl (truncated?) were not imported")
            self.report.count("messages_unreadable", bad)
        messages: dict[int, dict] = {}
        for item in raw:
            if not is_int(item.get("id")) or item["id"] < 1:
                self.report.anomaly(f"chat {chat_id}: a message without a numeric id was skipped")
                self.report.count("messages_unreadable")
                continue
            if item["id"] in messages:
                self.report.anomaly(f"chat {chat_id}: message id {item['id']} appears twice; the first is kept")
                continue
            messages[item["id"]] = item
        steps = read_workers(chat_id, folder, self.report)
        models, _ = read_jsonl(folder / "models.jsonl")
        rows = model_rows(self.source, folder, models, f"chat {chat_id}", self.report, build=False)
        self.changed(f"chat:{chat_id}", fingerprint(folder / "chat.json", folder / "messages.jsonl", folder / "models.jsonl",
                                                    *sorted((folder / "workers").glob("*.jsonl"))))
        with self.tx() as session:
            counts = lambda: (session.scalar(select(func.count()).select_from(Message).where(Message.chat_id == chat_id)),
                              session.scalar(select(func.count()).select_from(WorkerStep).where(WorkerStep.chat_id == chat_id)),
                              session.scalar(select(func.count()).select_from(ModelRecord).where(ModelRecord.chat_id == chat_id)),
                              session.scalar(select(func.count()).select_from(Chat).where(Chat.id == chat_id)),
                              session.scalar(select(func.count()).select_from(UsageEvent).where(UsageEvent.chat_id == chat_id)))
            before = counts()
            options = record.get("options") if isinstance(record.get("options"), dict) else {}
            session.execute(pg_insert(Chat).values(
                id=chat_id, title=str(record.get("title") or "New chat"), llm_model_id=record.get("llm_model_id"),
                options=jsonb(options), build_id=build_id, next_message_id=(max(messages) + 1) if messages else 1,
                created_at=to_dt(record.get("created_at")), updated_at=to_dt(record.get("updated_at"))).on_conflict_do_nothing())
            for chunk in chunks(sorted(messages), BATCH):
                session.execute(pg_insert(Message).values([
                    dict(chat_id=chat_id, id=i, body=jsonb(messages[i]),
                         created_at=to_dt(messages[i].get("created_at") if is_number(messages[i].get("created_at")) else record.get("created_at")))
                    for i in chunk]).on_conflict_do_nothing())
            if messages:
                session.execute(update(Chat).where(Chat.id == chat_id).values(
                    next_message_id=func.greatest(Chat.next_message_id, max(messages) + 1)))
            for worker_id, items in steps.items():
                for chunk in chunks(items, BATCH):
                    session.execute(pg_insert(WorkerStep).values([
                        dict(chat_id=chat_id, worker_id=worker_id, seq=seq, body=jsonb(item), created_at=to_dt(item.get("created_at")))
                        for seq, item in chunk]).on_conflict_do_nothing())
            if rows:
                session.execute(pg_insert(ModelRecord).values([{**r, "chat_id": chat_id} for r in rows]).on_conflict_do_nothing())
            events = self.usage_events(session, chat_id, messages)
            for chunk in chunks(events, BATCH):
                session.execute(pg_insert(UsageEvent).values(chunk))
            after = counts()
        for name, b, a in zip(("chats", "messages", "worker_steps", "chat_models", "usage_events"),
                              (before[3], before[0], before[1], before[2], before[4]),
                              (after[3], after[0], after[1], after[2], after[4])):
            self.report.count(f"{name}_added", a - b)
        self.report.count("messages_in_files", len(messages))
        self.report.count("worker_steps_in_files", sum(len(v) for v in steps.values()))
        self.report.count("chat_models_in_files", len(rows))

    def usage_events(self, session, chat_id: str, messages: dict[int, dict]) -> list[dict]:
        """One usage_event per message that carries a `_usage`, unless the live recorder already wrote one for it (same chat
        and message id, or the same worker): history imported once, live rows never doubled."""
        import settings
        from sqlalchemy import select
        from db.models import UsageEvent
        from pg_store import to_dt
        known_messages, known_workers = set(), set()
        for message_id, worker_id in session.execute(select(UsageEvent.message_id, UsageEvent.worker_id).where(UsageEvent.chat_id == chat_id)):
            if message_id is not None:
                known_messages.add(message_id)
            if worker_id is not None:
                known_workers.add(worker_id)
        rate = Decimal(str(settings.eur_per_usd()))
        events = []
        for message_id in sorted(messages):
            message = messages[message_id]
            usage = message.get("_usage")
            if not isinstance(usage, dict):
                continue
            worker = message.get("_worker")
            worker_id = worker.get("id") if isinstance(worker, dict) else (message["_subagent"] if isinstance(message.get("_subagent"), str) else None)
            if message_id in known_messages or (worker_id is not None and worker_id in known_workers):
                continue
            cost = usage.get("cost_usd")
            number = lambda key: int(usage.get(key)) if is_number(usage.get(key)) else 0
            events.append(dict(
                ts=to_dt(message.get("created_at")), kind="worker" if (worker or message.get("_subagent")) else "agent", chat_id=chat_id,
                build_id=None, message_id=message_id, worker_id=worker_id,
                model=(worker.get("model") if isinstance(worker, dict) else None) or message.get("_llm_model"),
                prompt=number("prompt"), completion=number("completion"), cache_read=number("cache_read"), cache_write=number("cache_write"),
                cost_usd=Decimal(str(round(cost, 6))) if is_number(cost) else None, eur_per_usd=rate))
        return events


def read_proposals(slug: str, folder: Path, report: Report) -> list[dict]:
    from pg_store import jsonb, to_dt
    import re
    found = []
    pdir = folder / "proposals"
    for path in sorted(pdir.glob("*.json")) if pdir.is_dir() else []:
        record = read_json(path)
        if record is None or not re.fullmatch(r"[0-9a-f]{12}", str(record.get("id", ""))) or not path.with_suffix(".mpd").is_file():
            report.anomaly(f"build {slug}: proposal file {path.name} is not valid or has no .mpd; skipped")
            continue
        cost = record.get("cost_eur")
        found.append(dict(id=record["id"], chat_id=record.get("chat_id") if isinstance(record.get("chat_id"), str) else None,
                          summary=jsonb(record.get("summary") if isinstance(record.get("summary"), dict) else {}),
                          cost_eur=Decimal(str(round(float(cost), 4))) if is_number(cost) else Decimal(0),
                          warnings=jsonb(record["warnings"]) if record.get("warnings") else None,
                          created_at=to_dt(record.get("created_at"))))
    return found



def read_workers(chat_id: str, folder: Path, report: Report) -> dict[str, list[tuple[int, dict]]]:
    import re
    found: dict[str, list[tuple[int, dict]]] = {}
    wdir = folder / "workers"
    for path in sorted(wdir.glob("*.jsonl")) if wdir.is_dir() else []:
        if not re.fullmatch(r"[0-9a-f]{8}", path.stem):
            report.anomaly(f"chat {chat_id}: worker file {path.name} has an unexpected name; skipped")
            continue
        records, bad = read_jsonl(path)
        if bad:
            report.anomaly(f"chat {chat_id}: {bad} unreadable line(s) in workers/{path.name}")
        seen, items = set(), []
        for position, item in enumerate(records, start=1):
            seq = item["id"] if is_int(item.get("id")) and item["id"] >= 1 else position
            if seq in seen:
                continue
            seen.add(seq)
            items.append((seq, item))
        found[path.stem] = items
    return found



def chunks(items: list, size: int) -> Iterator[list]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


# --------------------------------------------------------------------------------------------- verification

def close(a: float, b: float, tolerance: float = 2e-6) -> bool:
    return abs(float(a) - float(b)) <= tolerance


def verify(source: Source, report: Report) -> None:
    """Every entity of the files against the database. Extra rows in the database (made after the import) are fine and counted."""
    import builds
    import db
    from sqlalchemy import func, select
    from db.models import Build, BuildDraft, Chat, Message, ModelRecord, Proposal, UsageEvent, WorkerStep
    from pg_store import clean_json, file_sha256, to_float
    import usage as usage_module
    files = source.files
    index = builds.Index(files)
    slugs = builds.all_slugs(files)
    matched = {"builds": 0, "chats": 0, "messages": 0, "worker_steps": 0, "chat_models": 0, "build_versions": 0,
               "build_drafts": 0, "proposals": 0, "files_checked": 0}
    with db.session_scope() as session:
        for slug in slugs:
            record = builds.get(files, slug, index)
            row = session.get(Build, slug)
            if record is None:
                continue
            if row is None:
                report.mismatch(f"build {slug}: missing in the database")
                continue
            if row.title != str(record.get("title") or slug):
                report.mismatch(f"build {slug}: title differs")
            expected_source = dict(record.get("source") or {"kind": "empty"})
            if expected_source.get("kind") not in SOURCE_KINDS:
                expected_source["kind"] = "import"
            if row.source != clean_json(expected_source):
                report.mismatch(f"build {slug}: source differs")
            if not close(to_float(row.created_at), record.get("created_at") or 0):
                report.mismatch(f"build {slug}: created_at differs")
            matched["builds"] += 1
            folder = source.builds_dir / slug
            models, _ = read_jsonl(folder / "models.jsonl")
            wanted = model_rows(source, folder, models, f"build {slug}", Report("verify"), build=True)
            have = {r.version: r for r in session.scalars(select(ModelRecord).where(ModelRecord.build_id == slug))}
            for want in wanted:
                got = have.get(want["version"])
                if got is None or got.id != want["id"] or got.file != want["file"] or got.name != want["name"]:
                    report.mismatch(f"build {slug}: version {want['version']} differs or is missing")
                    continue
                matched["build_versions"] += 1
                check_file(report, matched, f"build {slug} v{want['version']}", source.data_dir, got)
            on_disk = builds.versions(slug)
            missing = [v["version"] for v in on_disk if v["version"] not in have]
            if missing:
                report.mismatch(f"build {slug}: model files without a version record: v{missing} (run the app once, or reconcile)")
            draft = read_json(folder / "draft.json")
            if draft is not None:
                got = session.get(BuildDraft, slug)
                if got is None:
                    report.mismatch(f"build {slug}: draft missing")
                else:
                    for name in DRAFT_COLUMNS:
                        if getattr(got, name) != (draft.get(name) if draft.get(name) is not None else (0 if name != "base_file" and name != "base_version" else None)):
                            report.mismatch(f"build {slug}: draft {name} differs")
                    extra = {k: v for k, v in draft.items() if k not in DRAFT_COLUMNS and k != "updated_at"}
                    if got.extra != clean_json(extra):
                        report.mismatch(f"build {slug}: draft extra keys differ")
                    matched["build_drafts"] += 1
            wanted_proposals = {p["id"] for p in read_proposals(slug, folder, Report("verify"))}
            got_proposals = set(session.scalars(select(Proposal.id).where(Proposal.build_id == slug)))
            if not wanted_proposals <= got_proposals:
                report.mismatch(f"build {slug}: proposals missing: {sorted(wanted_proposals - got_proposals)}")
            matched["proposals"] += len(wanted_proposals & got_proposals)
        for chat_id in source.chat_ids():
            folder = source.chats_dir / chat_id
            record = read_json(folder / "chat.json") or {}
            row = session.get(Chat, chat_id)
            if row is None:
                report.mismatch(f"chat {chat_id}: missing in the database")
                continue
            if row.title != str(record.get("title") or "New chat") or row.llm_model_id != record.get("llm_model_id"):
                report.mismatch(f"chat {chat_id}: title or model differs")
            if (row.options or {}) != clean_json(record.get("options") if isinstance(record.get("options"), dict) else {}):
                report.mismatch(f"chat {chat_id}: options differ")
            build_id = record.get("build_id") if record.get("build_id") in slugs else None
            if row.build_id != build_id:
                report.mismatch(f"chat {chat_id}: build link differs ({row.build_id!r} vs {build_id!r})")
            if not close(to_float(row.created_at), record.get("created_at") or 0):
                report.mismatch(f"chat {chat_id}: created_at differs")
            matched["chats"] += 1
            raw, _ = read_jsonl(folder / "messages.jsonl")
            expected = {}
            for item in raw:
                if is_int(item.get("id")) and item["id"] >= 1:
                    expected.setdefault(item["id"], item)
            bodies = {i: b for i, b in session.execute(select(Message.id, Message.body).where(Message.chat_id == chat_id, Message.id.in_(list(expected) or [0])))}
            if len(bodies) != len(expected):
                report.mismatch(f"chat {chat_id}: {len(expected)} messages in the files, {len(bodies)} of them in the database")
            elif canonical(list(expected.values())) != canonical(list(bodies.values())):
                report.mismatch(f"chat {chat_id}: the message hash differs")
            else:
                matched["messages"] += len(expected)
            total = session.scalar(select(func.count()).select_from(Message).where(Message.chat_id == chat_id))
            if total > len(expected):
                report.count("messages_only_in_database", total - len(expected))
            for worker_id, items in read_workers(chat_id, folder, Report("verify")).items():
                count = session.scalar(select(func.count()).select_from(WorkerStep).where(WorkerStep.chat_id == chat_id, WorkerStep.worker_id == worker_id,
                                                                                         WorkerStep.seq.in_([s for s, _ in items])))
                if count != len(items):
                    report.mismatch(f"chat {chat_id}: worker {worker_id} has {len(items)} steps in the files, {count} in the database")
                else:
                    matched["worker_steps"] += count
            models, _ = read_jsonl(folder / "models.jsonl")
            wanted = model_rows(source, folder, models, f"chat {chat_id}", Report("verify"), build=False)
            have = {r.id: r for r in session.scalars(select(ModelRecord).where(ModelRecord.chat_id == chat_id))}
            for want in wanted:
                got = have.get(want["id"])
                if got is None or got.file != want["file"] or got.version != want["version"]:
                    report.mismatch(f"chat {chat_id}: model record {want['id']} differs or is missing")
                    continue
                matched["chat_models"] += 1
                check_file(report, matched, f"chat {chat_id} model {want['id']}", source.data_dir, got)
            # the cost the chat's messages hold against the cost events of the chat
            totals = usage_module.usage_totals(list(expected.values()))
            events = session.execute(select(func.coalesce(func.sum(UsageEvent.cost_usd), 0), func.count()).where(UsageEvent.chat_id == chat_id)).one()
            if not close(float(events[0]), totals["cost_usd"], tolerance=1e-5 + 2e-6 * max(totals["steps"], 1)):
                report.mismatch(f"chat {chat_id}: cost in the files {totals['cost_usd']} USD, in usage_event {float(events[0])} USD")
    report.extra["matched"] = matched
    report.extra["chats_in_files"] = len(source.chat_ids())
    report.extra["builds_in_files"] = len(slugs)


def check_file(report: Report, matched: dict, label: str, data_dir: Path, row: Any) -> None:
    from pg_store import file_sha256
    path = data_dir / row.file
    if not path.is_file():
        report.count("model_files_missing")           # a deleted model: the chat keeps the record as history
        return
    matched["files_checked"] += 1
    if row.sha256 is None or file_sha256(path) != row.sha256:
        report.mismatch(f"{label}: sha256 of {row.file} differs from the record")


# --------------------------------------------------------------------------------------------- export (the inverse)

def export(source_dir: Path, target: Path, force: bool, report: Report) -> None:
    """Write the JSON layout from the database into `target`: chats/<id>/{chat.json,messages.jsonl,models.jsonl,workers/},
    builds/<slug>/{build.json,models.jsonl,draft.json,proposals/}. An existing file is left alone unless `force`."""
    import db
    from sqlalchemy import select
    from db.models import Build, BuildDraft, Chat, Message, ModelRecord, Proposal, WorkerStep
    from pg_store import to_float
    written = skipped = 0

    def put(path: Path, text: str) -> None:
        nonlocal written, skipped
        if path.exists() and not force:
            skipped += 1
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        written += 1

    def ref(folder: Path, file: str) -> str:
        return Path(os.path.relpath(source_dir / file, folder)).as_posix()

    def model_lines(folder: Path, rows: list) -> str:
        out = []
        for r in rows:
            record = {"id": r.id, "name": r.name, "model": ref(folder, r.file), "warnings": r.warnings or [],
                      "created_at": to_float(r.created_at), "version": r.version}
            if r.source:
                record["source"] = r.source
            if r.origin:
                record["origin"] = r.origin
            out.append(json.dumps(record, ensure_ascii=False))
        return "".join(line + "\n" for line in out)

    with db.session_scope() as session:
        for chat in session.scalars(select(Chat).order_by(Chat.id)):
            folder = target / "chats" / chat.id
            record = {"id": chat.id, "title": chat.title, "llm_model_id": chat.llm_model_id,
                      "created_at": to_float(chat.created_at), "updated_at": to_float(chat.updated_at)}
            if chat.options:
                record["options"] = chat.options
            if chat.build_id:
                record["build_id"] = chat.build_id
            put(folder / "chat.json", json.dumps(record, indent=2))
            bodies = list(session.scalars(select(Message.body).where(Message.chat_id == chat.id).order_by(Message.id)))
            if bodies:
                put(folder / "messages.jsonl", "".join(json.dumps(b, ensure_ascii=False) + "\n" for b in bodies))
            rows = session.scalars(select(ModelRecord).where(ModelRecord.chat_id == chat.id).order_by(ModelRecord.created_at, ModelRecord.version)).all()
            if rows:
                put(folder / "models.jsonl", model_lines(folder, rows))
            workers: dict[str, list] = {}
            for worker_id, body in session.execute(select(WorkerStep.worker_id, WorkerStep.body).where(WorkerStep.chat_id == chat.id)
                                                   .order_by(WorkerStep.worker_id, WorkerStep.seq)):
                workers.setdefault(worker_id, []).append(body)
            for worker_id, items in workers.items():
                put(folder / "workers" / f"{worker_id}.jsonl", "".join(json.dumps(b, ensure_ascii=False) + "\n" for b in items))
            report.count("chats_exported")
        for build in session.scalars(select(Build).order_by(Build.id)):
            folder = target / "builds" / build.id
            chats = list(session.scalars(select(Chat.id).where(Chat.build_id == build.id).order_by(Chat.created_at, Chat.id)))
            put(folder / "build.json", json.dumps({"id": "b-" + build.id, "title": build.title, "created_at": to_float(build.created_at),
                                                   "updated_at": to_float(build.updated_at), "chats": chats, "source": build.source}, indent=2, ensure_ascii=False))
            rows = session.scalars(select(ModelRecord).where(ModelRecord.build_id == build.id).order_by(ModelRecord.created_at, ModelRecord.version)).all()
            if rows:
                put(folder / "models.jsonl", model_lines(folder, rows))
            draft = session.get(BuildDraft, build.id)
            if draft is not None:
                record = {name: getattr(draft, name) for name in DRAFT_COLUMNS}
                record.update(draft.extra or {})
                record["updated_at"] = to_float(draft.updated_at)
                put(folder / "draft.json", json.dumps(record, indent=2))
            for p in session.scalars(select(Proposal).where(Proposal.build_id == build.id).order_by(Proposal.created_at)):
                record = {"id": p.id, "created_at": to_float(p.created_at), "summary": p.summary, "cost_eur": float(p.cost_eur), "chat_id": p.chat_id}
                if p.warnings:
                    record["warnings"] = p.warnings
                put(folder / "proposals" / f"{p.id}.json", json.dumps(record))
                mpd = source_dir / "builds" / build.id / "proposals" / f"{p.id}.mpd"
                if mpd.is_file() and not (folder / "proposals" / f"{p.id}.mpd").exists():
                    (folder / "proposals").mkdir(parents=True, exist_ok=True)
                    (folder / "proposals" / f"{p.id}.mpd").write_bytes(mpd.read_bytes())
            report.count("builds_exported")
    report.count("files_written", written)
    report.count("files_skipped_existing", skipped)


# --------------------------------------------------------------------------------------------- running it

def previous_sources() -> dict:
    import db
    from sqlalchemy import select
    from db.models import ImportRun
    with db.session_scope() as session:
        report = session.scalar(select(ImportRun.report).where(ImportRun.mode == "commit").order_by(ImportRun.id.desc()).limit(1))
    return (report or {}).get("sources", {}) if isinstance(report, dict) else {}


def record_run(report: Report, started: dt.datetime) -> None:
    import db
    from db.models import ImportRun
    with db.session_scope() as session:
        session.add(ImportRun(mode=report.mode, started_at=started, finished_at=dt.datetime.now(dt.timezone.utc), ok=report.ok,
                              report=json.loads(json.dumps(report.as_dict(), default=str))))


def mark_done(report: Report) -> None:
    import db
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from db.models import AppSetting
    value = {"done": True, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             "counts": {k: v for k, v in report.counts.items() if k.endswith("_in_files")}}
    statement = pg_insert(AppSetting).values(key="json_import", value=value)
    with db.session_scope() as session:
        session.execute(statement.on_conflict_do_update(index_elements=["key"], set_={"value": statement.excluded.value, "updated_at": dt.datetime.now(dt.timezone.utc)}))


def run_import(data_dir: Path, dry_run: bool) -> Report:
    report = Report("dry-run" if dry_run else "commit")
    started = dt.datetime.now(dt.timezone.utc)
    importer = Importer(Source(data_dir), dry_run, report, previous_sources())
    try:
        importer.run()
    finally:
        importer.finish()
    if not dry_run:
        from builds import reconcile
        from pg_store import PgChatStore
        import settings
        report.count("version_records_adopted_from_files", reconcile(PgChatStore(data_dir / "chats", data_dir / "output")))
    record_run(report, started)
    return report


def run_verify(data_dir: Path) -> Report:
    report = Report("verify")
    started = dt.datetime.now(dt.timezone.utc)
    verify(Source(data_dir), report)
    record_run(report, started)
    return report


def write_report_file(data_dir: Path, *reports: Report) -> Path:
    folder = data_dir / REPORT_DIR_NAME
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"report-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps([r.as_dict() for r in reports], indent=2, default=str), encoding="utf-8")
    return path


def import_and_verify(data_dir: Path, dry_first: bool = True) -> tuple[bool, list[Report]]:
    """dry run, commit, verify; marks the migration done when all of it passed. Used by the CLI and by the first start."""
    reports = []
    if dry_first:
        reports.append(run_import(data_dir, dry_run=True))
    reports.append(run_import(data_dir, dry_run=False))
    verified = run_verify(data_dir)
    reports.append(verified)
    ok = verified.ok
    if ok:
        mark_done(reports[-2])
    write_report_file(data_dir, *reports)
    return ok, reports


def first_start(data_dir: Path) -> Optional[bool]:
    """The automatic import at start (LDRAW_NOVA_DB_IMPORT=auto): only when the files hold state and no import was recorded."""
    import storage
    if not storage.auto_import() or storage.import_done() or not storage.json_state_present():
        return None
    return import_and_verify(data_dir)[0]


def summary_text(report: Report) -> str:
    lines = [f"== {report.mode}: {'OK' if report.ok else 'MISMATCHES'} =="]
    lines += [f"  {name}: {value}" for name, value in sorted(report.counts.items())]
    if report.extra.get("matched"):
        lines.append("  verified (files == database): " + ", ".join(f"{k}={v}" for k, v in report.extra["matched"].items()))
    for key in ("chats_in_files", "builds_in_files"):
        if key in report.extra:
            lines.append(f"  {key}: {report.extra[key]}")
    for text in report.anomalies:
        lines.append(f"  anomaly: {text}")
    for text in report.mismatches:
        lines.append(f"  MISMATCH: {text}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="migrate_json", description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="parse and insert in a rolled-back transaction (default)")
    mode.add_argument("--commit", action="store_true", help="import, then verify")
    mode.add_argument("--verify", action="store_true", help="compare the files with the database")
    mode.add_argument("--export", metavar="DIR", help="write the JSON layout from the database into DIR")
    parser.add_argument("--force", action="store_true", help="with --export: overwrite files that exist")
    parser.add_argument("--data-dir", help="the data folder to read (default LDRAW_NOVA_DATA_DIR or /data)")
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
    data_dir = Path(args.data_dir) if args.data_dir else settings.DATA_DIR
    if args.export:
        report = Report("export")
        export(data_dir, Path(args.export), args.force, report)
        reports = [report]
        ok = True
    elif args.verify:
        reports = [run_verify(data_dir)]
        ok = reports[0].ok
    elif args.commit:
        ok, reports = import_and_verify(data_dir)
    else:
        reports = [run_import(data_dir, dry_run=True)]
        ok = True
    for report in reports:
        print(json.dumps(report.as_dict(), indent=2, default=str) if args.json else summary_text(report))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
