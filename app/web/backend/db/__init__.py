"""The application database (PostgreSQL via SQLAlchemy 2.0 and psycopg 3; schema by Alembic).

    from db import session_scope, best_effort      # transactions
    from db import models                          # every table of the schema

Plan and schema reference: docs/plans/postgres/. Today it holds the request log and the cost history; chats, builds and
the parts collection move here in later phases.
"""
from .engine import best_effort, configure, database_url, dispose, get_engine, reset, session_scope, status

__all__ = ["best_effort", "configure", "database_url", "dispose", "get_engine", "reset", "session_scope", "status"]
