"""Chats, messages, worker steps and published-model records in PostgreSQL, behind the interface of store.ChatStore.

Same methods, same return values; the routers, the agent loop and the tools do not know which one they hold. What stays
a file: a chat's folder (renders, workspace, editor state) and its agent work folder, created next to the row, and
every model file. A build's workspace key (`b-<slug>`) is not a chat: its message and worker files stay files, its
published models are the build's versions (`model_record.build_id`).

A message's `body` is the whole record the file store kept (`id`, `created_at`, `role`, `_usage`, `_worker`, ...), so
nothing a caller reads changes. Per-call spend is written to `usage_event` by usage_log.record next to each step, with the
chat and message id: this store does not write it a second time.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

import db
from db.models import Build, Chat, Message, ModelRecord, WorkerStep
from store import BUILD_PREFIX, ChatStore

CHAT_FIELDS = ("title", "llm_model_id", "options", "build_id")


def to_dt(value: Any) -> dt.datetime:
    try:
        return dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return dt.datetime.now(dt.timezone.utc)


def to_float(value: dt.datetime) -> float:
    return value.timestamp()


def clean_json(value: Any) -> Any:
    """jsonb cannot hold U+0000 (nor NaN/Infinity): replace them, everything else is returned as it was."""
    if isinstance(value, str):
        return value.replace("\x00", "�") if "\x00" in value else value
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, list):
        return [clean_json(v) for v in value]
    if isinstance(value, dict):
        return {clean_json(k): clean_json(v) for k, v in value.items()}
    return value


def jsonb(value: Any) -> Any:
    """A value for a jsonb column (see clean_json)."""
    return clean_json(value)


def file_sha256(path: Path) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def part_count(path: Path) -> Optional[int]:
    """Parts in a model file (leaf placements, submodels expanded), None when it cannot be read."""
    try:
        import workspace
        return sum(workspace.part_usage(path.read_text(encoding="utf-8", errors="replace")).values())
    except Exception:  # noqa: BLE001 - a number for the table, never a reason to fail a save
        return None


class PgWorkerSink:
    """A worker's message sink in `worker_step`: the same contract as agent_loop.FileSink."""

    def __init__(self, chat_id: str, worker_id: str):
        self.chat_id, self.worker_id = chat_id, worker_id
        self._items: list[dict] = []

    def messages(self) -> list[dict]:
        return list(self._items)

    def add(self, message: dict) -> int:
        record = {"id": len(self._items) + 1, **message}
        with db.session_scope() as session:
            session.add(WorkerStep(chat_id=self.chat_id, worker_id=self.worker_id, seq=record["id"], body=jsonb(record),
                                   created_at=to_dt(time.time())))
        self._items.append(record)
        return record["id"]


class PgChatStore(ChatStore):
    pg = True

    # --- chats ---------------------------------------------------------------

    @staticmethod
    def _chat(row: Chat) -> dict:
        chat = {"id": row.id, "title": row.title, "llm_model_id": row.llm_model_id,
                "created_at": to_float(row.created_at), "updated_at": to_float(row.updated_at)}
        if row.options:
            chat["options"] = row.options
        if row.build_id:
            chat["build_id"] = row.build_id
        return chat

    def create_chat(self, title: str = "New chat", llm_model_id: Optional[str] = None, build_id: Optional[str] = None) -> dict:
        chat_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        now = to_dt(time.time())
        with db.session_scope() as session:
            row = Chat(id=chat_id, title=title, llm_model_id=llm_model_id, options={}, build_id=build_id or None,
                       created_at=now, updated_at=now)
            session.add(row)
            session.flush()
            chat = self._chat(row)
        self.chat_dir(chat_id).mkdir(parents=True, exist_ok=True)
        self.work_dir(chat_id).mkdir(parents=True, exist_ok=True)
        return chat

    def get_chat(self, chat_id: str) -> Optional[dict]:
        if not self.valid_id(chat_id) or self.is_build(chat_id):
            return None
        with db.session_scope() as session:
            row = session.get(Chat, chat_id)
            return self._chat(row) if row else None

    def list_chats(self) -> list[dict]:
        with db.session_scope() as session:
            rows = session.scalars(select(Chat).order_by(Chat.updated_at.desc(), Chat.id.desc())).all()
            return [self._chat(row) for row in rows]

    def update_chat(self, chat_id: str, **fields: Any) -> None:
        if not self.valid_id(chat_id) or self.is_build(chat_id):
            return
        values = {k: v for k, v in fields.items() if k in CHAT_FIELDS}
        if "options" in values:
            values["options"] = jsonb(values["options"] or {})
        with db.session_scope() as session:
            session.execute(update(Chat).where(Chat.id == chat_id).values(**values, updated_at=to_dt(time.time())))

    def delete_chat(self, chat_id: str) -> None:
        """Removes the chat's rows (messages, worker steps, model records), its folder and its work folder. The model
        files stay in data/generated, and so does the spend in usage_event."""
        if self.valid_id(chat_id) and not self.is_build(chat_id):
            with db.session_scope() as session:
                session.execute(delete(Chat).where(Chat.id == chat_id))
        shutil.rmtree(self.chat_dir(chat_id), ignore_errors=True)
        shutil.rmtree(self.work_dir(chat_id), ignore_errors=True)

    # --- messages ------------------------------------------------------------

    def add_message(self, chat_id: str, message: dict) -> int:
        if self.is_build(chat_id):
            return super().add_message(chat_id, message)
        now = time.time()
        with db.session_scope() as session:
            # One statement takes the next number and locks the chat row: concurrent writers queue up on it.
            taken = session.execute(update(Chat).where(Chat.id == chat_id)
                                    .values(next_message_id=Chat.next_message_id + 1, updated_at=to_dt(now))
                                    .returning(Chat.next_message_id)).scalar()
            if taken is None:
                raise FileNotFoundError(f"no chat {chat_id!r}")
            msg_id = taken - 1
            record = {"id": msg_id, "created_at": now, **message}
            record["id"] = msg_id
            stamp = record["created_at"] if isinstance(record["created_at"], (int, float)) else now
            session.add(Message(chat_id=chat_id, id=msg_id, body=jsonb(record), created_at=to_dt(stamp)))
        return msg_id

    def messages(self, chat_id: str) -> list[dict]:
        if self.is_build(chat_id):
            return super().messages(chat_id)
        if not self.valid_id(chat_id):
            raise ValueError(f"invalid chat id {chat_id!r}")
        with db.session_scope() as session:
            return list(session.scalars(select(Message.body).where(Message.chat_id == chat_id).order_by(Message.id)))

    # --- workers -------------------------------------------------------------

    def worker_sink(self, chat_id: str, worker_id: str):
        if self.is_build(chat_id):
            return super().worker_sink(chat_id, worker_id)
        return PgWorkerSink(chat_id, worker_id)

    def worker_messages(self, chat_id: str, worker_id: str) -> Optional[list[dict]]:
        if self.is_build(chat_id):
            return super().worker_messages(chat_id, worker_id)
        with db.session_scope() as session:
            rows = list(session.scalars(select(WorkerStep.body).where(WorkerStep.chat_id == chat_id, WorkerStep.worker_id == worker_id)
                                        .order_by(WorkerStep.seq)))
        return rows or None

    # --- models the chat (or the build) produced -------------------------------

    def file_value(self, path: Path) -> str:
        """The `file` column: relative to the data folder (generated/x-v1.mpd), the absolute path if it lies outside."""
        try:
            return Path(path).resolve().relative_to(self.chats_dir.parent.resolve()).as_posix()
        except ValueError:
            return Path(path).as_posix()

    def file_path(self, value: str) -> Path:
        return self.chats_dir.parent / value

    def _owner(self, chat_id: str):
        """(column, key) of the model_record owner: a chat, or the build behind a b-<slug> key."""
        if self.is_build(chat_id):
            return ModelRecord.build_id, chat_id[len(BUILD_PREFIX):]
        return ModelRecord.chat_id, chat_id

    def _record(self, chat_id: str, row: ModelRecord) -> dict:
        record = {"id": row.id, "name": row.name, "model": self.ref(chat_id, self.file_path(row.file)),
                  "warnings": row.warnings or [], "created_at": to_float(row.created_at), "version": row.version}
        if row.source:
            record["source"] = row.source
        if row.origin:
            record["origin"] = row.origin
        return record

    def add_model(self, chat_id: str, name: str, model_path: Path, warnings: list[str],
                  source: Optional[str] = None, origin: Optional[str] = None) -> dict:
        build = self.is_build(chat_id)
        column, key = self._owner(chat_id)
        values = dict(id=secrets.token_hex(6), name=name, file=self.file_value(model_path), sha256=file_sha256(Path(model_path)),
                      parts=part_count(Path(model_path)), warnings=jsonb(list(warnings or [])), source=source or None,
                      origin=origin or None, created_at=to_dt(time.time()))
        owner = Build.id if build else Chat.id
        with db.session_scope() as session:
            session.execute(select(owner).where(owner == key).with_for_update())        # two saves at once get two versions
            if build:       # a build's version is the number in its file name (<slug>-vN.mpd)
                match = re.search(r"-v(\d+)$", Path(model_path).stem)
                version = int(match.group(1)) if match else (session.scalar(
                    select(func.coalesce(func.max(ModelRecord.version), 0)).where(column == key)) or 0) + 1
                statement = pg_insert(ModelRecord).values(**values, build_id=key, version=version)
                statement = statement.on_conflict_do_update(index_elements=["build_id", "version"],
                                                            index_where=ModelRecord.build_id.is_not(None),
                                                            set_={k: statement.excluded[k] for k in values})
            else:
                version = (session.scalar(select(func.count()).select_from(ModelRecord).where(column == key)) or 0) + 1
                statement = pg_insert(ModelRecord).values(**values, chat_id=key, version=version)
            session.execute(statement)
            return self._record(chat_id, session.execute(select(ModelRecord).where(column == key, ModelRecord.version == version)).scalar_one())

    def models(self, chat_id: str) -> list[dict]:
        if not self.valid_id(chat_id):
            raise ValueError(f"invalid chat id {chat_id!r}")
        column, key = self._owner(chat_id)
        with db.session_scope() as session:
            rows = session.scalars(select(ModelRecord).where(column == key).order_by(ModelRecord.created_at, ModelRecord.version)).all()
            return [self._record(chat_id, row) for row in rows]

    # --- builds ----------------------------------------------------------------

    def build_ids(self) -> list[str]:
        with db.session_scope() as session:
            return list(session.scalars(select(Build.id).order_by(Build.id)))
