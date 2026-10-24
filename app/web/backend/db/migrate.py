"""`alembic upgrade head` at application start, under a PostgreSQL advisory lock.

Two app processes (or a --reload restart racing the old worker) never run a migration at the same time: the second
waits for the lock, then finds nothing left to do. Also runnable by hand: `python -m db.migrate` from web/backend.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from . import engine as engine_module

log = logging.getLogger("db.migrate")

BACKEND_DIR = Path(__file__).resolve().parents[1]
LOCK_KEY = 0x6F6264_01          # "obd" + 1: arbitrary, only has to be the same in every app process


def alembic_config(connection=None) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def upgrade(engine: Engine | None = None, revision: str = "head") -> None:
    """Bring the schema to `revision`, holding the advisory lock for the whole run."""
    engine = engine or engine_module.get_engine()
    if engine is None:
        raise RuntimeError("no database is configured")
    with engine.connect() as connection:
        connection.execute(text("select pg_advisory_lock(:key)"), {"key": LOCK_KEY})
        connection.commit()
        try:
            command.upgrade(alembic_config(connection), revision)
            connection.commit()
        finally:
            connection.rollback()
            connection.execute(text("select pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
            connection.commit()


def upgrade_when_ready(timeout_s: float = 30.0, engine: Engine | None = None) -> bool:
    """upgrade(), retrying while the database is still starting. Returns False (and logs) instead of raising."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            upgrade(engine)
            log.info("database schema is up to date")
            return True
        except RuntimeError as exc:                       # not configured: nothing to wait for
            log.info("database migrations skipped: %s", exc)
            return False
        except Exception as exc:  # noqa: BLE001
            if time.monotonic() >= deadline:
                log.error("database migrations failed: %s: %s", exc.__class__.__name__, str(exc)[:300])
                return False
            time.sleep(1.0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(0 if upgrade_when_ready() else 1)
