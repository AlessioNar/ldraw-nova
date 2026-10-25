"""SQLAlchemy 2.0 models for every table of docs/plans/postgres/sql/0001_init.sql (the reference schema).

Later phases move chats, builds and the parts collection onto these; today only app_setting, usage_event and
request_log are written. Keep this file and the Alembic revisions in step: a revision is generated with
`alembic revision --autogenerate`, then reviewed by hand (see migrations/README.md).
"""
from __future__ import annotations

import datetime as dt
import decimal
from typing import Any, Optional

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Computed, ForeignKey, Identity, Index, Integer, MetaData,
                        Numeric, Text, func, text)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP
from sqlalchemy.sql.naming import conv
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# PostgreSQL's own default names, so the schema matches the reference SQL file name for name.
metadata = MetaData(naming_convention={
    "pk": "%(table_name)s_pkey",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "uq": "%(table_name)s_%(column_0_name)s_key",
    "ck": "%(table_name)s_%(constraint_name)s_check",
})


class Base(DeclarativeBase):
    metadata = metadata
    type_annotation_map = {dt.datetime: TIMESTAMP(timezone=True)}


NOW = func.now()
EMPTY_OBJECT = text("'{}'::jsonb")
EMPTY_ARRAY = text("'[]'::jsonb")


# ---------------------------------------------------------------- settings
class AppSetting(Base):
    """Non-secret settings only; keys and endpoints stay in /config."""
    __tablename__ = "app_setting"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=NOW)


# ---------------------------------------------------------------- builds and chats
class Build(Base):
    __tablename__ = "build"
    __table_args__ = (
        CheckConstraint("id ~ '^[a-z0-9][a-z0-9-]{0,59}$'", name="id"),
        CheckConstraint("source_kind IN ('generator','ai','import','editor','empty')", name="source_kind"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    source: Mapped[Any] = mapped_column(JSONB, server_default=text("""'{"kind":"import"}'::jsonb"""))
    source_kind: Mapped[Optional[str]] = mapped_column(Text, Computed("source->>'kind'", persisted=True))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=NOW)
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=NOW)


class Chat(Base):
    __tablename__ = "chat"
    __table_args__ = (
        CheckConstraint("id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$' AND id !~ '^b-'", name="id"),
        Index("chat_updated_idx", text("updated_at DESC")),
        Index("chat_build_idx", "build_id", postgresql_where=text("build_id IS NOT NULL")),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, server_default="New chat")
    llm_model_id: Mapped[Optional[str]] = mapped_column(Text)
    options: Mapped[Any] = mapped_column(JSONB, server_default=EMPTY_OBJECT)
    build_id: Mapped[Optional[str]] = mapped_column(ForeignKey("build.id", ondelete="SET NULL"))
    next_message_id: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[dt.datetime] = mapped_column(server_default=NOW)
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=NOW)


class Message(Base):
    __tablename__ = "message"
    chat_id: Mapped[str] = mapped_column(ForeignKey("chat.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=NOW)
    body: Mapped[Any] = mapped_column(JSONB)
    role: Mapped[Optional[str]] = mapped_column(Text, Computed("body->>'role'", persisted=True))


class WorkerStep(Base):
    __tablename__ = "worker_step"
    chat_id: Mapped[str] = mapped_column(ForeignKey("chat.id", ondelete="CASCADE"), primary_key=True)
    worker_id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=NOW)
    body: Mapped[Any] = mapped_column(JSONB)


class ModelRecord(Base):
    """A chat's published models AND a build's versions."""
    __tablename__ = "model_record"
    __table_args__ = (
        CheckConstraint("version > 0", name="version"),
        CheckConstraint("origin IN ('generator','ai','editor','import')", name="origin"),
        CheckConstraint("num_nonnulls(chat_id, build_id) = 1", name=conv("model_record_check")),
        Index("model_record_build_version", "build_id", "version", unique=True, postgresql_where=text("build_id IS NOT NULL")),
        Index("model_record_chat_version", "chat_id", "version", unique=True, postgresql_where=text("chat_id IS NOT NULL")),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    chat_id: Mapped[Optional[str]] = mapped_column(ForeignKey("chat.id", ondelete="CASCADE"))
    build_id: Mapped[Optional[str]] = mapped_column(ForeignKey("build.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    file: Mapped[str] = mapped_column(Text)
    sha256: Mapped[Optional[str]] = mapped_column(Text)
    parts: Mapped[Optional[int]] = mapped_column(Integer)
    warnings: Mapped[Any] = mapped_column(JSONB, server_default=EMPTY_ARRAY)
    source: Mapped[Optional[str]] = mapped_column(Text)
    origin: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=NOW)


class BuildDraft(Base):
    __tablename__ = "build_draft"
    build_id: Mapped[str] = mapped_column(ForeignKey("build.id", ondelete="CASCADE"), primary_key=True)
    base_version: Mapped[Optional[int]] = mapped_column(Integer)
    base_file: Mapped[Optional[str]] = mapped_column(Text)
    base_revision: Mapped[int] = mapped_column(Integer, server_default="0")
    saved_revision: Mapped[int] = mapped_column(Integer, server_default="0")
    base_undo_depth: Mapped[int] = mapped_column(Integer, server_default="0")
    extra: Mapped[Any] = mapped_column(JSONB, server_default=EMPTY_OBJECT)     # forked, drift, sources, ... (draft.json keys)
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=NOW)


class Proposal(Base):
    __tablename__ = "proposal"
    __table_args__ = (
        CheckConstraint("id ~ '^[0-9a-f]{12}$'", name="id"),
        Index("proposal_build_idx", "build_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    build_id: Mapped[str] = mapped_column(ForeignKey("build.id", ondelete="CASCADE"))
    chat_id: Mapped[Optional[str]] = mapped_column(Text)          # no foreign key: like the JSON file, it outlives its chat
    summary: Mapped[Any] = mapped_column(JSONB, server_default=EMPTY_OBJECT)
    cost_eur: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 4), server_default=text("0"))
    warnings: Mapped[Optional[Any]] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=NOW)


# ---------------------------------------------------------------- parts collection
class PartSource(Base):
    __tablename__ = "part_source"
    __table_args__ = (
        CheckConstraint("id ~ '^[a-z0-9][a-z0-9-]{0,63}$'", name="id"),
        CheckConstraint("kind IN ('import','set','loose')", name="kind"),
        Index("part_source_name_key", text("lower(name)"), unique=True),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, server_default="import")
    schema_version: Mapped[int] = mapped_column(Integer, server_default="2")
    source_filename: Mapped[Optional[str]] = mapped_column(Text)
    origin: Mapped[Optional[Any]] = mapped_column(JSONB)
    set_info: Mapped[Optional[Any]] = mapped_column(JSONB)
    import_meta: Mapped[Optional[Any]] = mapped_column(JSONB)
    totals: Mapped[Optional[Any]] = mapped_column(JSONB)
    warnings: Mapped[Any] = mapped_column(JSONB, server_default=EMPTY_ARRAY)
    unmapped_parts: Mapped[Any] = mapped_column(JSONB, server_default=EMPTY_ARRAY)
    unmapped_colours: Mapped[Any] = mapped_column(JSONB, server_default=EMPTY_ARRAY)
    csv_file: Mapped[Optional[str]] = mapped_column(Text)            # unused: the upload is csv_text since revision 0003
    csv_text: Mapped[Optional[str]] = mapped_column(Text)            # the original upload (<id>.csv), as sent
    extra: Mapped[Any] = mapped_column(JSONB, server_default=EMPTY_OBJECT)     # top-level keys of the source JSON with no column
    rows_hash: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=NOW)
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=NOW)


class PartRow(Base):
    """Exact rows: LDraw part number + LDraw colour code."""
    __tablename__ = "part_row"
    __table_args__ = (CheckConstraint("qty >= 0", name="qty"), Index("part_row_part_idx", "part", "colour"))
    source_id: Mapped[str] = mapped_column(ForeignKey("part_source.id", ondelete="CASCADE"), primary_key=True)
    part: Mapped[str] = mapped_column(Text, primary_key=True)
    colour: Mapped[int] = mapped_column(Integer, primary_key=True)
    qty: Mapped[int] = mapped_column(Integer)
    rb_part: Mapped[Optional[str]] = mapped_column(Text)
    rb_colour: Mapped[Optional[int]] = mapped_column(Integer)
    pos: Mapped[int] = mapped_column(Integer, server_default="0")              # position in the source's row list
    extra: Mapped[Optional[Any]] = mapped_column(JSONB)                        # row keys with no column


class PartReview(Base):
    """Approximate items (colour families): never counted as exact."""
    __tablename__ = "part_review"
    __table_args__ = (CheckConstraint("qty > 0", name="qty"),)
    source_id: Mapped[str] = mapped_column(ForeignKey("part_source.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    part: Mapped[str] = mapped_column(Text)
    family: Mapped[Optional[str]] = mapped_column(Text)
    candidates: Mapped[list[int]] = mapped_column(ARRAY(Integer), server_default=text("'{}'::integer[]"))
    qty: Mapped[int] = mapped_column(Integer)
    brickit_part: Mapped[Optional[str]] = mapped_column(Text)
    note: Mapped[Optional[str]] = mapped_column(Text)
    pos: Mapped[int] = mapped_column(Integer, server_default="0")
    extra: Mapped[Optional[Any]] = mapped_column(JSONB)


class PartNote(Base):
    """A free-text note per part ("blue tub"), later converted to locations."""
    __tablename__ = "part_note"
    part: Mapped[str] = mapped_column(Text, primary_key=True)
    note: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=NOW)


class PartLog(Base):
    """A source's change log (<id>.log.jsonl): one row per entry, grouped in batches; the entry is the whole log line.
    Replaces the reference schema's part_change (revision 0003): the real entries have integer batches and more ops."""
    __tablename__ = "part_log"
    __table_args__ = (Index("part_log_source_batch", "source_id", "batch"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("part_source.id", ondelete="CASCADE"))
    batch: Mapped[int] = mapped_column(Integer)
    entry: Mapped[Any] = mapped_column(JSONB)


class PartNoteLog(Base):
    """The change log of the part notes (.collection-notes.log.jsonl)."""
    __tablename__ = "part_note_log"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    entry: Mapped[Any] = mapped_column(JSONB)


class PartReservation(Base):
    """A model the owner built holds its parts (.collection-reservations.json); `body` is the reservation record."""
    __tablename__ = "part_reservation"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True))        # insertion order
    body: Mapped[Any] = mapped_column(JSONB)


# ---------------------------------------------------------------- cost history and request log
class UsageEvent(Base):
    """One LLM call. Append-only, no foreign keys: deleting a chat keeps the spend."""
    __tablename__ = "usage_event"
    __table_args__ = (
        CheckConstraint("kind IN ('agent','worker','understand','other')", name="kind"),
        Index("usage_event_ts_idx", "ts"),
        Index("usage_event_chat_idx", "chat_id", postgresql_where=text("chat_id IS NOT NULL")),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(server_default=NOW)
    kind: Mapped[str] = mapped_column(Text)
    chat_id: Mapped[Optional[str]] = mapped_column(Text)
    build_id: Mapped[Optional[str]] = mapped_column(Text)
    message_id: Mapped[Optional[int]] = mapped_column(Integer)
    worker_id: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[Optional[str]] = mapped_column(Text)
    prompt: Mapped[int] = mapped_column(Integer, server_default="0")
    completion: Mapped[int] = mapped_column(Integer, server_default="0")
    cache_read: Mapped[int] = mapped_column(Integer, server_default="0")
    cache_write: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric(12, 6))      # NULL = unpriced model
    eur_per_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric(8, 4))


class RequestLog(Base):
    """One row per describe/estimate request. `sentence` is NULL unless the owner opted in (keep_request_text)."""
    __tablename__ = "request_log"
    __table_args__ = (
        Index("request_log_ts_idx", text("ts DESC")),
        Index("request_log_route_idx", "route", text("ts DESC")),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(server_default=NOW)
    surface: Mapped[Optional[str]] = mapped_column(Text)
    build_kind: Mapped[Optional[str]] = mapped_column(Text)
    route: Mapped[Optional[str]] = mapped_column(Text)
    decided_by: Mapped[Optional[str]] = mapped_column(Text)
    family: Mapped[Optional[str]] = mapped_column(Text)
    kind: Mapped[Optional[str]] = mapped_column(Text)
    generator: Mapped[Optional[str]] = mapped_column(Text)
    text_sha: Mapped[Optional[str]] = mapped_column(Text)
    text_len: Mapped[Optional[int]] = mapped_column(Integer)
    lang: Mapped[Optional[str]] = mapped_column(Text)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    applied: Mapped[Optional[bool]] = mapped_column(Boolean)
    undone_within_60s: Mapped[Optional[bool]] = mapped_column(Boolean)
    went_to_agent: Mapped[Optional[bool]] = mapped_column(Boolean)
    usage_event_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("usage_event.id", ondelete="SET NULL"))
    sentence: Mapped[Optional[str]] = mapped_column(Text)
    fields: Mapped[Any] = mapped_column(JSONB, server_default=EMPTY_OBJECT)


# ---------------------------------------------------------------- bookkeeping
class ImportRun(Base):
    """The JSON importer: one row per run, report inside."""
    __tablename__ = "import_run"
    __table_args__ = (CheckConstraint("mode IN ('dry-run','commit','verify')", name="mode"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    started_at: Mapped[dt.datetime] = mapped_column(server_default=NOW)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column()
    mode: Mapped[str] = mapped_column(Text)
    ok: Mapped[Optional[bool]] = mapped_column(Boolean)
    report: Mapped[Any] = mapped_column(JSONB, server_default=EMPTY_OBJECT)


