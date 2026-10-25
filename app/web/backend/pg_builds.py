"""Build records, draft metadata and proposal metadata in PostgreSQL (the `build`, `build_draft` and `proposal` tables).

builds.py and workspace.py call these when their store is a PgChatStore; the functions return the dicts the JSON files
held (`build.json`, `draft.json`, `proposals/<id>.json`), so nothing above them changes. Versions are still the model
files in data/generated (what exists is what is on disk); their metadata (origin, sha256, part count) is in
`model_record`, written by PgChatStore.add_model and adopted for files nobody recorded by `reconcile`.
"""
from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

import db
from db.models import Build, BuildDraft, Chat, ModelRecord, Proposal
from pg_store import file_sha256, jsonb, part_count, to_dt, to_float

SOURCE_KINDS = ("generator", "ai", "import", "editor", "empty")
DRAFT_COLUMNS = ("base_version", "base_file", "base_revision", "saved_revision", "base_undo_depth")


# --- build.json --------------------------------------------------------------------------------------------------

def read_build(key: str) -> Optional[dict]:
    """The build's record as build.json had it, `chats` being the chats whose build_id is this build."""
    with db.session_scope() as session:
        row = session.get(Build, key)
        if row is None:
            return None
        chats = list(session.scalars(select(Chat.id).where(Chat.build_id == key).order_by(Chat.created_at, Chat.id)))
        return {"id": "b-" + row.id, "title": row.title, "created_at": to_float(row.created_at),
                "updated_at": to_float(row.updated_at), "chats": chats, "source": row.source}


def write_build(slug: str, record: dict) -> None:
    source = dict(record.get("source") or {"kind": "empty"})
    if source.get("kind") not in SOURCE_KINDS:
        source["kind"] = "import"
    values = dict(title=record.get("title") or slug, source=jsonb(source), created_at=to_dt(record.get("created_at") or time.time()),
                  updated_at=to_dt(record.get("updated_at") or time.time()))
    statement = pg_insert(Build).values(id=slug, **values)
    with db.session_scope() as session:
        session.execute(statement.on_conflict_do_update(index_elements=["id"], set_={k: statement.excluded[k] for k in values}))


def forget_build(slug: str) -> None:
    """The build is deleted: its row goes, and with it its versions' records, draft and proposals; its chats stay, detached."""
    with db.session_scope() as session:
        session.execute(delete(Build).where(Build.id == slug))


# --- draft.json --------------------------------------------------------------------------------------------------

def read_draft(slug: str) -> Optional[dict]:
    with db.session_scope() as session:
        row = session.get(BuildDraft, slug)
        if row is None:
            return None
        record = {name: getattr(row, name) for name in DRAFT_COLUMNS}
        record.update(row.extra or {})
        record["updated_at"] = to_float(row.updated_at)
        return record


def write_draft(slug: str, record: dict) -> None:
    values = {name: record.get(name) for name in DRAFT_COLUMNS}
    for name in ("base_revision", "saved_revision", "base_undo_depth"):
        values[name] = values[name] or 0
    values["extra"] = jsonb({k: v for k, v in record.items() if k not in DRAFT_COLUMNS and k != "updated_at"})
    values["updated_at"] = to_dt(record["updated_at"])
    statement = pg_insert(BuildDraft).values(build_id=slug, **values)
    with db.session_scope() as session:
        session.execute(statement.on_conflict_do_update(index_elements=["build_id"], set_={k: statement.excluded[k] for k in values}))


# --- proposals -----------------------------------------------------------------------------------------------------

def _proposal(row: Proposal) -> dict:
    record = {"id": row.id, "created_at": to_float(row.created_at), "summary": row.summary, "cost_eur": float(row.cost_eur),
              "chat_id": row.chat_id}
    if row.warnings:
        record["warnings"] = row.warnings
    return record


def add_proposal(slug: str, record: dict) -> None:
    with db.session_scope() as session:
        session.add(Proposal(id=record["id"], build_id=slug, chat_id=record.get("chat_id"), summary=jsonb(record["summary"]),
                             cost_eur=record["cost_eur"], warnings=jsonb(record["warnings"]) if record.get("warnings") else None,
                             created_at=to_dt(record["created_at"])))


def proposals(slug: str) -> list[dict]:
    """Oldest first."""
    with db.session_scope() as session:
        rows = session.scalars(select(Proposal).where(Proposal.build_id == slug).order_by(Proposal.created_at, Proposal.id)).all()
        return [_proposal(row) for row in rows]


def has_proposal(slug: str, pid: str) -> bool:
    with db.session_scope() as session:
        return session.scalar(select(Proposal.id).where(Proposal.id == pid, Proposal.build_id == slug)) is not None


def remove_proposal(slug: str, pid: str) -> None:
    with db.session_scope() as session:
        session.execute(delete(Proposal).where(Proposal.id == pid, Proposal.build_id == slug))


# --- keeping the version records in step with the files ------------------------------------------------------------

def reconcile(store, groups: dict[str, dict[int, Path]]) -> int:
    """Adopt the model files of every recorded build that has no model_record yet (files dropped into data/generated,
    or saved before the database was used). Returns the number of rows added. Builds without a record stay derived
    from their files, as before."""
    import builds
    added = 0
    with db.session_scope() as session:
        known = set(session.scalars(select(Build.id)))
        have = set(session.execute(select(ModelRecord.build_id, ModelRecord.version).where(ModelRecord.build_id.is_not(None))).all())
    if not known:
        return 0
    index = builds.Index(store)
    rows = []
    for slug in sorted(known & set(groups)):
        for version, path in sorted(groups[slug].items()):
            if (slug, version) in have:
                continue
            rows.append(dict(id=secrets.token_hex(6), build_id=slug, version=version, name=slug, file=store.file_value(path),
                             sha256=file_sha256(path), parts=part_count(path), warnings=[],
                             origin=builds.origin_of(store, slug, path, index),
                             created_at=to_dt(path.stat().st_mtime if path.exists() else time.time())))
    if rows:
        with db.session_scope() as session:
            added = len(session.execute(pg_insert(ModelRecord).values(rows).on_conflict_do_nothing().returning(ModelRecord.id)).all())
    return added
