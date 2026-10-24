"""The database connection: one SQLAlchemy engine, built from a root-only password file.

Nothing here reads the password from, or writes it to, an environment variable: the agent sandbox runs with a scrubbed
environment, and `docker inspect` and /api/environment would show a variable. The file lives on a volume the
server (root) reads and agent code (uid 999) cannot. Host, database and user default to the compose service; the
overrides below are for development and tests, never needed in the image.

    /run/secrets/db_password           the password of the `obd_app` role (LDRAW_NOVA_DB_PASSWORD_FILE overrides)
    LDRAW_NOVA_DB_HOST / _PORT / _NAME / _USER      defaults db / 5432 / obd / obd_app
    LDRAW_NOVA_DATABASE_URL            a complete URL, used instead of all of the above (tests, `alembic` on a laptop)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional, TypeVar

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

log = logging.getLogger("db")
T = TypeVar("T")

DEFAULT_PASSWORD_FILE = "/run/secrets/db_password"
RETRY_AFTER_S = 30            # after a failed best-effort write, skip the database for this long

_lock = threading.Lock()
_engine: Optional[Engine] = None
_url: Optional[URL] = None
_down_until = 0.0


def database_url() -> Optional[URL]:
    """The URL to connect to, or None when no database is configured (no URL and no password file)."""
    explicit = os.environ.get("LDRAW_NOVA_DATABASE_URL", "").strip()
    if explicit:
        url = make_url(explicit)
        return url.set(drivername="postgresql+psycopg") if url.drivername in ("postgresql", "postgres") else url
    path = Path(os.environ.get("LDRAW_NOVA_DB_PASSWORD_FILE", DEFAULT_PASSWORD_FILE))
    try:
        password = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not password:
        return None
    return URL.create("postgresql+psycopg", username=os.environ.get("LDRAW_NOVA_DB_USER", "obd_app"), password=password,
                      host=os.environ.get("LDRAW_NOVA_DB_HOST", "db"), port=int(os.environ.get("LDRAW_NOVA_DB_PORT", "5432")),
                      database=os.environ.get("LDRAW_NOVA_DB_NAME", "obd"))


def configure(url: str | URL | None = None) -> Optional[Engine]:
    """(Re)build the engine from `url`, or from the environment and the password file. Returns None if unconfigured."""
    global _engine, _url, _down_until
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine, _down_until = None, 0.0
        _url = make_url(url) if isinstance(url, str) else url or database_url()
        if _url is not None:
            # Timeouts turn a stuck statement or transaction into an error instead of a frozen UI.
            _engine = create_engine(_url, pool_size=5, max_overflow=3, pool_pre_ping=True, pool_recycle=1800,
                                    connect_args={"connect_timeout": 3, "application_name": "obd",
                                                  "options": "-c statement_timeout=15000 -c idle_in_transaction_session_timeout=30000"})
        return _engine


def get_engine() -> Optional[Engine]:
    """The engine, created on first use; None when the app has no database configured."""
    return _engine if _engine is not None else configure(_url)


def dispose() -> None:
    """Close the pooled connections (shutdown). The configuration is kept: the next use builds a new engine."""
    global _engine
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None


def reset() -> None:
    """Forget the engine and the configuration (tests): the next use reads the environment again."""
    global _engine, _url, _down_until
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = _url = None
        _down_until = 0.0


@contextmanager
def session_scope() -> Iterator[Session]:
    """A session in one explicit transaction: committed on a clean exit, rolled back on any error."""
    engine = get_engine()
    if engine is None:
        raise RuntimeError("no database is configured")
    with Session(engine, expire_on_commit=False) as session, session.begin():
        yield session


def best_effort(fn: Callable[[Session], T], what: str = "database write") -> Optional[T]:
    """Run fn(session) in a transaction for a log-style write that must never break a request.

    Returns its result, or None when there is no database or it failed. After a failure the database is skipped for
    RETRY_AFTER_S seconds, so a stopped database costs one short timeout, not one per request.
    """
    global _down_until
    if get_engine() is None or time.monotonic() < _down_until:
        return None
    try:
        with session_scope() as session:
            return fn(session)
    except Exception as exc:  # noqa: BLE001 - telemetry and cost bookkeeping never fail the caller
        _down_until = time.monotonic() + RETRY_AFTER_S
        log.warning("%s skipped: %s", what, exc.__class__.__name__)
        return None


def status() -> dict:
    """{"state": "ok" | "unavailable" | "off", "detail": ...} for /api/environment. Never raises."""
    engine = get_engine()
    if engine is None:
        return {"state": "off", "detail": "no database is configured"}
    try:
        with engine.connect() as connection:
            version = connection.execute(text("select current_setting('server_version')")).scalar_one()
            head = connection.execute(text("select version_num from alembic_version")).scalar()                 if connection.execute(text("select to_regclass('alembic_version') is not null")).scalar_one() else None
        return {"state": "ok", "detail": f"PostgreSQL {version}, schema {head or 'not migrated'}"}
    except Exception as exc:  # noqa: BLE001
        return {"state": "unavailable", "detail": exc.__class__.__name__}
