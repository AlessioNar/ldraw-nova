"""Which storage the app uses for chats, builds, drafts and proposals: PostgreSQL or the JSON files.

    LDRAW_NOVA_STORAGE=auto       (default) PostgreSQL when the database is reachable and migrated to the current schema
                                  AND the files hold nothing that is not in it yet; the files otherwise
    LDRAW_NOVA_STORAGE=files      always the JSON files (a db-less install, or a way back after the migration)
    LDRAW_NOVA_STORAGE=postgres   PostgreSQL even if the files hold data nobody imported (falls back to the files, with
                                  an error in the log, when the database is not usable)

"Nothing that is not in it yet" is the safety net of `auto`: an installation that still has chats and builds as files
and has not run the importer (`python -m migrate_json --commit`, see docs/system/database.md) keeps working on the
files instead of showing an empty list. Once `app_setting['json_import']` says the import is done, or the data folder
holds no chats and builds, the database is used. Model files, editor state and agent work folders are files either way.

    LDRAW_NOVA_DB_IMPORT=auto     run the importer by itself at the first start (default `manual`: never)
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from sqlalchemy import text

import db
import settings

log = logging.getLogger("storage")

_decision: Optional[bool] = None


def mode() -> str:
    value = os.environ.get("LDRAW_NOVA_STORAGE", "auto").strip().lower()
    return value if value in ("auto", "files", "postgres") else "auto"


def auto_import() -> bool:
    return os.environ.get("LDRAW_NOVA_DB_IMPORT", "manual").strip().lower() == "auto"


def schema_ready() -> bool:
    """The database answers and its schema is at the newest Alembic revision."""
    engine = db.get_engine()
    if engine is None:
        return False
    try:
        from alembic.script import ScriptDirectory
        from db import migrate
        head = ScriptDirectory.from_config(migrate.alembic_config()).get_current_head()
        with engine.connect() as connection:
            if not connection.execute(text("select to_regclass('alembic_version') is not null")).scalar_one():
                return False
            return connection.execute(text("select version_num from alembic_version")).scalar() == head
    except Exception as exc:  # noqa: BLE001
        log.warning("database not usable for storage: %s", exc.__class__.__name__)
        return False


def json_state_present() -> bool:
    """Chats or builds exist as files (a chat.json or build.json), so an import is pending unless it was done."""
    for folder, name in ((settings.CHATS_DIR, "chat.json"), (settings.DATA_DIR / "builds", "build.json")):
        try:
            if folder.is_dir() and any((p / name).is_file() for p in folder.iterdir() if p.is_dir()):
                return True
        except OSError:
            continue
    return False


def import_done() -> bool:
    engine = db.get_engine()
    if engine is None:
        return False
    try:
        with engine.connect() as connection:
            value = connection.execute(text("select value from app_setting where key = 'json_import'")).scalar()
        return bool(isinstance(value, dict) and value.get("done"))
    except Exception:  # noqa: BLE001
        return False


def use_database() -> bool:
    """The decision, made once per process (and again after refresh())."""
    global _decision
    if _decision is None:
        _decision = _decide()
    return _decision


def refresh() -> bool:
    global _decision
    _decision = None
    return use_database()


def _decide() -> bool:
    chosen = mode()
    if chosen == "files":
        log.info("JSON files (LDRAW_NOVA_STORAGE=files)")
        return False
    if not schema_ready():
        (log.error if chosen == "postgres" else log.info)("JSON files (the database is not available or not migrated)")
        return False
    if chosen == "auto" and json_state_present() and not import_done():
        log.warning("JSON files. The database is ready but the chats and builds on disk were not imported; run "
                    "`python -m migrate_json --dry-run`, then `--commit` (docs/system/database.md), or set LDRAW_NOVA_STORAGE=postgres")
        return False
    log.info("PostgreSQL")
    return True


def status() -> dict:
    """For /api/environment: {"backend": "postgres" | "files", "mode": ..., "json_import": "done" | "pending" | "none"}."""
    pending = json_state_present()
    return {"backend": "postgres" if use_database() else "files", "mode": mode(),
            "json_import": "done" if import_done() else ("pending" if pending else "none")}
